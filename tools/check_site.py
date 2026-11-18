"""Verificação automática do site do Manda Currículo.

Uso:
    python tools/check_site.py                 confere tudo e gera capturas em tools/screens/
    python tools/check_site.py --sem-capturas  confere tudo, sem gerar capturas

Precisa de um Python com o Playwright e o Chromium dele instalados (por exemplo, o .venv
do programa). Não usa a internet: serve o site em 127.0.0.1 no mesmo caminho do GitHub
Pages (/manda-curriculo/), simula a API de versões do GitHub e bloqueia qualquer outro
pedido externo. Imprime cada verificação e termina com "OK" (código 0) ou com a lista
das falhas (código 1).

Além das páginas, confere o histórico do git (todo autor e committer com o e-mail privado
do GitHub, sem nunca imprimir um e-mail), dados pessoais nos arquivos e no histórico, o
contraste de todo texto e o anel de foco de todos os links e botões nos dois temas, o aviso
"Está no celular?" (só em celular de verdade), o alto contraste do Windows, a folha das
páginas internas em tela larga e o nome que o leitor de tela lê.

Dados pessoais: além de e-mails, telefones e CPFs, procura os marcadores de
tools/marcadores.local.txt (um por linha; o arquivo fica fora do git) ou da variável
MANDA_MARCADORES (separados por ";"). Nunca imprime o que achou, só o nome do arquivo.
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

RAIZ = Path(__file__).resolve().parents[1]
CAPTURAS = RAIZ / "tools" / "screens"
PREFIXO = "/manda-curriculo/"
SITE_PUBLICO = "https://waynermoraes12.github.io/manda-curriculo/"
REPO = "WaynerMoraes12/manda-curriculo"
ARQUIVO = "MandaCurriculo-win-Setup.exe"
DOWNLOAD_ULTIMA = f"https://github.com/{REPO}/releases/latest/download/{ARQUIVO}"
PAGINA_VERSOES = f"https://github.com/{REPO}/releases"
DOWNLOAD_PRE = f"https://github.com/{REPO}/releases/download/v0.2.0-beta.1/{ARQUIVO}"
API = "https://api.github.com/"

LARGURAS_ROLAGEM = (320, 640, 768, 1440)  # 640 = tela de 1280 px com zoom de 200%
LARGURAS_CAPTURA = (320, 768, 1440)
TEMAS = ("light", "dark")
LIMITE_INICIAL = 300_000  # bytes
NOREPLY = "@users.noreply.github.com"  # e-mail privado do GitHub: o único aceito nos commits

# Ilustrações do passo a passo: a menor letra precisa de pelo menos 12 px na tela. As versões
# "-celular" aparecem até 480 px (imagem com 288 px a 320 px de tela); as outras, a partir de
# 481 px (imagem com cerca de 442 px).
LETRA_MINIMA_PX = 12
LARGURA_IMAGEM_CELULAR = 288
LARGURA_IMAGEM_MENOR = 442

# Contraste mínimo (WCAG): texto 4,5:1; anel de foco e outros elementos que não são texto 3:1.
CONTRASTE_TEXTO = 4.5
CONTRASTE_FOCO = 3.0
FOCAVEIS = "a[href], button, summary, [tabindex]:not([tabindex='-1'])"

# Funções de cor usadas dentro da página: lê rgb()/rgba()/color(srgb ...), calcula o contraste
# e acha o fundo (o ancestral mais próximo com cor de fundo).
JS_CORES = r"""(() => {
  const cor = (texto) => {
    const rgb = /rgba?\(([^)]+)\)/.exec(texto || '');
    if (rgb) {
      const p = rgb[1].split(/[\s,\/]+/).filter(Boolean).map(Number);
      return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1];
    }
    const srgb = /color\(srgb\s+([^)]+)\)/.exec(texto || '');
    if (srgb) {
      const p = srgb[1].split(/[\s\/]+/).filter(Boolean).map(Number);
      return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1];
    }
    return null;
  };
  const canal = (v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const luz = (c) => 0.2126 * canal(c[0]) + 0.7152 * canal(c[1]) + 0.0722 * canal(c[2]);
  const contraste = (a, b) => {
    const [x, y] = [luz(a), luz(b)].sort((p, q) => q - p);
    return Math.round(((x + 0.05) / (y + 0.05)) * 100) / 100;
  };
  const fundo = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const c = cor(getComputedStyle(n).backgroundColor);
      if (c && c[3] > 0) return c;
    }
    return [255, 255, 255, 1];
  };
  const nome = (el) => (el.getAttribute('aria-label') || el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 50);
  return { cor, contraste, fundo, nome };
})()"""

CHAVES_STATUS = {
    "pausar_tudo": bool,
    "fontes_pausadas": list,
    "plataformas_pausadas": list,
    "versao_minima": str,
    "aviso": str,
}

PALAVRAS_PROIBIDAS = (
    ("score", re.compile(r"\bscore\b", re.IGNORECASE)),
    ("LLM", re.compile(r"\bLLMs?\b", re.IGNORECASE)),
    ("ATS", re.compile(r"\bATS\b", re.IGNORECASE)),
    ("pipeline", re.compile(r"\bpipeline\b", re.IGNORECASE)),
    ("garantia de emprego", re.compile(r"garantia\s+de\s+emprego", re.IGNORECASE)),
    ("garantia de entrevista", re.compile(r"garantia\s+de\s+entrevista", re.IGNORECASE)),
)
# A única identificação permitida é o usuário público do GitHub. O nome (início do usuário, antes do
# sobrenome) é derivado dele, para o próprio verificador não escrever dado pessoal por extenso.
USUARIO_PUBLICO = "waynermoraes12"
_NOME = 6
IDENTIFICACAO = re.compile(re.escape(USUARIO_PUBLICO[:_NOME]) + "(?!" + re.escape(USUARIO_PUBLICO[_NOME:]) + ")",
                           re.IGNORECASE)

# Dados pessoais. Os marcadores (sobrenome, cidade, empregadores...) ficam fora do git, num
# arquivo local ou numa variável de ambiente; o que for achado nunca é impresso.
MARCADORES_LOCAIS = RAIZ / "tools" / "marcadores.local.txt"
VARIAVEL_MARCADORES = "MANDA_MARCADORES"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
EMAILS_PERMITIDOS = ("@users.noreply.github.com", "@example.com", "@example.org", "@example.net")
# Telefone e CPF não podem estar dentro de um número maior (versão, data, lei), mas podem
# terminar a frase: "(11) 90000-0001." conta.
TELEFONE = re.compile(r"(?<!\d)(?<!\d[.,/])(?:\+?55[\s.-]?)?\(?[1-9]\d\)?[\s.-]?9?\d{4}[\s.-]?\d{4}(?!\d)(?![.,/]\d)")
CPF = re.compile(r"(?<!\d)(?<!\d[.,/])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?!\d)(?![.,/]\d)")
EXTENSOES_BINARIAS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".pdf", ".exe", ".zip"}
ARMAZENAMENTO = re.compile(r"document\.cookie|localStorage|sessionStorage|indexedDB")

# Navegador de celular de verdade e Windows com tela de toque (o aviso "Está no celular?" só
# pode aparecer no primeiro).
AGENTE_ANDROID = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Mobile Safari/537.36"
)
AGENTE_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Rótulos em inglês que o leitor de tela precisa ler em inglês (lang="en").
TERMOS_EM_INGLES = ("Get API key", "Create API key", "Report a vulnerability", "Assets", "Issue")

# Elementos que carregam recursos (script, estilo, fonte, imagem): nunca de outro domínio.
REL_DE_RECURSO = {"stylesheet", "icon", "preload", "modulepreload", "prefetch", "manifest", "apple-touch-icon", "mask-icon"}
TAGS_DE_RECURSO = {"script", "img", "source", "video", "audio", "iframe", "embed", "object", "track", "image", "use"}

API_VERSAO_NORMAL = [
    {
        "tag_name": "v0.1.0",
        "draft": False,
        "prerelease": False,
        "assets": [{"name": ARQUIVO, "size": 84 * 1024 * 1024, "browser_download_url": f"https://github.com/{REPO}/releases/download/v0.1.0/{ARQUIVO}"}],
    }
]
API_SO_PRE = [
    {
        "tag_name": "v0.2.0-beta.1",
        "draft": False,
        "prerelease": True,
        "assets": [{"name": ARQUIVO, "size": 5 * 1024 * 1024 + 400_000, "browser_download_url": DOWNLOAD_PRE}],
    }
]


# ---------------------------------------------------------------- relatório


@dataclass
class Relatorio:
    passaram: int = 0
    falhas: list[str] = field(default_factory=list)

    def conferir(self, nome: str, condicao: bool, detalhe: str = "") -> bool:
        if condicao:
            self.passaram += 1
            print(f"ok     {nome}")
        else:
            texto = f"{nome}: {detalhe}" if detalhe else nome
            self.falhas.append(texto)
            print(f"FALHA  {texto}")
        return condicao


# ---------------------------------------------------------------- leitura estática


class Analisador(HTMLParser):
    """Junta o que as verificações estáticas precisam de uma página."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang: str | None = None
        self.titulo = ""
        self.descricao: str | None = None
        self.base: str | None = None
        self.niveis: list[int] = []
        self.ids: set[str] = set()
        self.referencias: list[tuple[str, str, str, set[str]]] = []
        self.imagens: list[tuple[str | None, str | None]] = []
        self._no_titulo = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        atributos = {nome: (valor or "") for nome, valor in attrs}
        if "id" in atributos:
            self.ids.add(atributos["id"])
        if tag == "html":
            self.lang = atributos.get("lang")
        elif tag == "title":
            self._no_titulo = True
        elif tag == "meta" and atributos.get("name") == "description":
            self.descricao = atributos.get("content", "")
        elif tag == "base":
            self.base = atributos.get("href")
        elif tag == "img":
            self.imagens.append((atributos.get("src"), dict(attrs).get("alt")))
        if re.fullmatch(r"h[1-6]", tag):
            self.niveis.append(int(tag[1]))
        rel = set(atributos.get("rel", "").lower().split())
        for nome in ("href", "src", "srcset", "data", "poster", "xlink:href"):
            if nome in atributos:
                self.referencias.append((tag, nome, atributos[nome], rel))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._no_titulo = False

    def handle_data(self, data: str) -> None:
        if self._no_titulo:
            self.titulo += data


def analisar(caminho: Path) -> Analisador:
    analisador = Analisador()
    analisador.feed(caminho.read_text(encoding="utf-8"))
    analisador.close()
    return analisador


def externo(valor: str) -> bool:
    partes = urlsplit(valor.strip())
    return bool(partes.scheme or partes.netloc or valor.strip().startswith("//"))


def url_da_pagina(pagina: Path) -> str:
    return "https://site.local" + PREFIXO + pagina.relative_to(RAIZ).as_posix()


def resolver(url_base: str, valor: str) -> tuple[Path | None, str]:
    """Converte uma referência interna no arquivo do site e no fragmento (#id)."""
    alvo = urlsplit(urljoin(url_base, valor.strip()))
    caminho = unquote(alvo.path)
    if alvo.netloc != "site.local" or not (caminho + "/").startswith(PREFIXO):
        return None, alvo.fragment
    relativo = caminho[len(PREFIXO):]
    arquivo = RAIZ / relativo
    if relativo == "" or relativo.endswith("/"):
        arquivo = arquivo / "index.html"
    return arquivo, alvo.fragment


def urls_do_srcset(valor: str) -> list[str]:
    return [parte.strip().split()[0] for parte in valor.split(",") if parte.strip()]


def conferir_paginas(rel: Relatorio, paginas: list[Path]) -> None:
    cache: dict[Path, Analisador] = {pagina: analisar(pagina) for pagina in paginas}

    def ids_de(arquivo: Path) -> set[str]:
        if arquivo not in cache:
            cache[arquivo] = analisar(arquivo)
        return cache[arquivo].ids

    for pagina in paginas:
        nome = pagina.name
        dados = cache[pagina]
        rel.conferir(f"{nome}: lang=\"pt-BR\"", dados.lang == "pt-BR", f"lang={dados.lang!r}")
        rel.conferir(f"{nome}: <title>", len(dados.titulo.strip()) >= 10, f"título {dados.titulo.strip()!r}")
        descricao = (dados.descricao or "").strip()
        rel.conferir(f"{nome}: meta description", 50 <= len(descricao) <= 300, f"{len(descricao)} caracteres")
        rel.conferir(f"{nome}: um único <h1>", dados.niveis.count(1) == 1, f"{dados.niveis.count(1)} <h1>")

        saltos = [f"h{a}->h{b}" for a, b in zip(dados.niveis, dados.niveis[1:]) if b > a + 1]
        primeiro_ok = bool(dados.niveis) and dados.niveis[0] == 1
        rel.conferir(f"{nome}: títulos em ordem", primeiro_ok and not saltos, f"saltos {saltos}, primeiro h{dados.niveis[:1]}")

        sem_alt = [src for src, alt in dados.imagens if alt is None or not alt.strip()]
        rel.conferir(f"{nome}: imagens com texto alternativo", not sem_alt, f"sem alt: {sem_alt}")

        url_base = urljoin(url_da_pagina(pagina), dados.base) if dados.base else url_da_pagina(pagina)
        quebrados: list[str] = []
        de_fora: list[str] = []
        for tag, atributo, valor, rels in dados.referencias:
            valores = urls_do_srcset(valor) if atributo == "srcset" else [valor]
            for item in valores:
                if not item.strip() or item.startswith(("mailto:", "tel:")):
                    continue
                if externo(item):
                    carrega = tag in TAGS_DE_RECURSO or (tag == "link" and rels & REL_DE_RECURSO)
                    if carrega:
                        de_fora.append(f"<{tag} {atributo}={item}>")
                    continue
                arquivo, fragmento = resolver(url_base, item)
                if arquivo is None or not arquivo.is_file():
                    quebrados.append(item)
                elif fragmento and arquivo.suffix == ".html" and fragmento not in ids_de(arquivo):
                    quebrados.append(f"{item} (sem id #{fragmento})")
        rel.conferir(f"{nome}: links internos resolvem", not quebrados, f"quebrados: {quebrados}")
        rel.conferir(f"{nome}: nada carregado de outro domínio", not de_fora, f"externos: {de_fora}")

        # Com <base>, um link só de fragmento ("#conteudo") vai para o documento da base, e não
        # para a própria página: o "Pular para o conteúdo" da 404 levava à página inicial.
        propria = urlsplit(url_da_pagina(pagina))
        para_fora = [
            valor
            for _, atributo, valor, _ in dados.referencias
            if atributo == "href" and valor.strip().startswith("#")
            and urlsplit(urljoin(url_base, valor.strip()))[:3] != propria[:3]
        ]
        rel.conferir(
            f"{nome}: links só de fragmento ficam na própria página",
            not para_fora,
            f"com <base href={dados.base!r}>, vão para outro documento: {para_fora}",
        )

        texto = pagina.read_text(encoding="utf-8")
        achadas = [rotulo for rotulo, padrao in PALAVRAS_PROIBIDAS if padrao.search(texto)]
        rel.conferir(f"{nome}: sem palavras proibidas", not achadas, f"achadas: {achadas}")
        rel.conferir(f"{nome}: identificação só pelo usuário público", not IDENTIFICACAO.search(texto))
        scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", texto, flags=re.DOTALL | re.IGNORECASE)
        rel.conferir(f"{nome}: scripts da página sem cookies nem armazenamento", not any(ARMAZENAMENTO.search(s) for s in scripts))


def conferir_css_e_js(rel: Relatorio) -> None:
    padrao_url = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
    padrao_import = re.compile(r"@import\s+(?:url\()?\s*['\"]?([^'\")\s;]+)", re.IGNORECASE)
    sem_comentarios = lambda texto: re.sub(r"/\*.*?\*/", "", texto, flags=re.DOTALL)  # noqa: E731
    for css in sorted((RAIZ / "assets" / "css").glob("*.css")):
        texto = sem_comentarios(css.read_text(encoding="utf-8"))
        problemas = []
        for _, endereco in padrao_url.findall(texto):
            if endereco.startswith("data:"):
                continue
            if externo(endereco):
                problemas.append(f"externo {endereco}")
            elif not (css.parent / endereco).resolve().is_file():
                problemas.append(f"não existe {endereco}")
        for endereco in padrao_import.findall(texto):
            problemas.append(f"@import {endereco}")
        rel.conferir(f"{css.name}: recursos locais e existentes", not problemas, "; ".join(problemas))

    tokens = sem_comentarios((RAIZ / "assets" / "css" / "tokens.css").read_text(encoding="utf-8"))
    autocontido = "url(" not in tokens and "@import" not in tokens
    tem_escuro = "prefers-color-scheme: dark" in tokens and 'data-tema="escuro"' in tokens
    rel.conferir("tokens.css: autocontido e com modo escuro", autocontido and tem_escuro)

    site_css = (RAIZ / "assets" / "css" / "site.css").read_text(encoding="utf-8")
    rel.conferir("site.css: respeita prefers-reduced-motion", "prefers-reduced-motion" in site_css)

    for js in sorted((RAIZ / "assets" / "js").glob("*.js")):
        texto = js.read_text(encoding="utf-8")
        achadas = [rotulo for rotulo, padrao in PALAVRAS_PROIBIDAS if padrao.search(texto)]
        sem_rastro = not ARMAZENAMENTO.search(texto)
        rel.conferir(f"{js.name}: sem palavras proibidas", not achadas, f"achadas: {achadas}")
        rel.conferir(f"{js.name}: sem cookies nem armazenamento", sem_rastro)


def conferir_arquivos(rel: Relatorio, paginas: list[Path]) -> None:
    try:
        status = json.loads((RAIZ / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        rel.conferir("status.json: exatamente as 5 chaves", False, str(erro))
    else:
        chaves_ok = isinstance(status, dict) and set(status) == set(CHAVES_STATUS)
        rel.conferir("status.json: exatamente as 5 chaves", chaves_ok, f"chaves {sorted(status) if isinstance(status, dict) else status}")
        if chaves_ok:
            tipos_ok = all(isinstance(status[chave], tipo) for chave, tipo in CHAVES_STATUS.items())
            listas_ok = all(isinstance(item, str) for chave in ("fontes_pausadas", "plataformas_pausadas") for item in status[chave])
            versao_ok = bool(re.fullmatch(r"\d+\.\d+\.\d+", status["versao_minima"]))
            rel.conferir("status.json: tipos e versão mínima válidos", tipos_ok and listas_ok and versao_ok)

    try:
        manifesto = json.loads((RAIZ / "site.webmanifest").read_text(encoding="utf-8"))
        icones = [RAIZ / icone["src"] for icone in manifesto.get("icons", [])]
        faltando = [str(icone.relative_to(RAIZ)) for icone in icones if not icone.is_file()]
        rel.conferir("site.webmanifest: válido e com ícones existentes", bool(icones) and not faltando, f"faltando {faltando}")
    except (OSError, ValueError, KeyError) as erro:
        rel.conferir("site.webmanifest: válido e com ícones existentes", False, str(erro))

    try:
        # Leitura simples das tags <loc>, sem parser de XML: o arquivo é do próprio site.
        texto_sitemap = (RAIZ / "sitemap.xml").read_text(encoding="utf-8")
        if "<urlset" not in texto_sitemap or "</urlset>" not in texto_sitemap:
            raise ValueError("sitemap.xml sem <urlset>")
        locais = [local.strip() for local in re.findall(r"<loc>(.*?)</loc>", texto_sitemap, re.DOTALL)]
        listadas = set()
        problemas = []
        for endereco in locais:
            if not endereco.startswith(SITE_PUBLICO):
                problemas.append(endereco)
                continue
            relativo = endereco[len(SITE_PUBLICO):] or "index.html"
            if not (RAIZ / relativo).is_file():
                problemas.append(endereco)
            listadas.add(relativo)
        esperadas = {pagina.name for pagina in paginas if pagina.name != "404.html"}
        rel.conferir("sitemap.xml: lista todas as páginas", not problemas and listadas == esperadas, f"problemas {problemas}, listadas {sorted(listadas)}")
    except (OSError, ValueError) as erro:
        rel.conferir("sitemap.xml: lista todas as páginas", False, str(erro))

    robots = (RAIZ / "robots.txt").read_text(encoding="utf-8") if (RAIZ / "robots.txt").is_file() else ""
    rel.conferir("robots.txt: aponta o sitemap", f"Sitemap: {SITE_PUBLICO}sitemap.xml" in robots)
    rel.conferir(".nojekyll existe", (RAIZ / ".nojekyll").is_file())
    rel.conferir("og.png existe", (RAIZ / "assets" / "img" / "og.png").is_file())


def conferir_ilustracoes(rel: Relatorio) -> None:
    """As letras das ilustrações do passo a passo continuam legíveis na menor tela em que aparecem."""
    pagina = (RAIZ / "baixar.html").read_text(encoding="utf-8")
    usadas = sorted(set(re.findall(r"assets/img/([\w-]+\.svg)", pagina)))
    rel.conferir("baixar.html: passo a passo com ilustrações", len(usadas) >= 4, f"ilustrações {usadas}")
    for nome in usadas:
        arquivo = RAIZ / "assets" / "img" / nome
        if not arquivo.is_file():
            continue  # o link quebrado já aparece na verificação de links internos
        texto = arquivo.read_text(encoding="utf-8")
        caixa = re.search(r'viewBox="\s*[\d.]+\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s*"', texto)
        letras = [float(valor) for valor in re.findall(r'font-size="([\d.]+)"', texto)]
        if not caixa or not letras:
            rel.conferir(f"{nome}: letras legíveis", False, "sem viewBox ou sem font-size")
            continue
        largura_tela = LARGURA_IMAGEM_CELULAR if nome.endswith("-celular.svg") else LARGURA_IMAGEM_MENOR
        menor = min(letras) * largura_tela / float(caixa.group(1))
        rel.conferir(
            f"{nome}: menor letra com {LETRA_MINIMA_PX} px ou mais",
            menor >= LETRA_MINIMA_PX,
            f"{menor:.1f} px com a imagem a {largura_tela} px",
        )


def conferir_git(rel: Relatorio) -> None:
    """Nenhum e-mail pessoal no histórico: todo autor e committer usa o e-mail privado do GitHub.

    Os e-mails nunca são impressos, nem quando a verificação falha.
    """
    if not (RAIZ / ".git").exists():
        print("       (sem .git: verificação do histórico pulada)")
        return
    try:
        historico = subprocess.run(
            ["git", "-C", str(RAIZ), "log", "--format=%ae%n%ce"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout.split()
        proximo = subprocess.run(
            ["git", "-C", str(RAIZ), "config", "user.email"],
            capture_output=True, text=True, encoding="utf-8",
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as erro:
        rel.conferir("git: commits só com o e-mail privado do GitHub", False, f"não deu para ler o histórico ({type(erro).__name__})")
        return
    fora = {email for email in historico if not email.lower().endswith(NOREPLY)}
    rel.conferir(
        f"git: commits só com o e-mail privado do GitHub ({NOREPLY})",
        bool(historico) and not fora,
        f"{len(fora)} e-mail(s) fora do padrão (não mostrados)",
    )
    rel.conferir(
        "git: o próximo commit também usa o e-mail privado do GitHub",
        proximo.lower().endswith(NOREPLY),
        "user.email fora do padrão (não mostrado); configure só neste repositório, sem --global",
    )


# ---------------------------------------------------------------- dados pessoais


class TextoVisivel(HTMLParser):
    """Texto que uma pessoa lê numa página ou num SVG: conteúdo (sem script e estilo) e os
    atributos que viram texto (alt, title, aria-label, descrições)."""

    ATRIBUTOS = {"alt", "title", "aria-label", "content", "placeholder", "value"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.partes: list[str] = []
        self._ignorar = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._ignorar += 1
        self.partes.extend(valor for nome, valor in attrs if nome in self.ATRIBUTOS and valor)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._ignorar:
            self._ignorar -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignorar:
            self.partes.append(data)


def _normalizar(texto: str) -> str:
    """Minúsculas e sem acento; o usuário público do GitHub (permitido) sai antes da busca."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    simples = "".join(letra for letra in sem_acento if not unicodedata.combining(letra)).lower()
    return simples.replace(USUARIO_PUBLICO, " ")


def _marcadores() -> list[re.Pattern[str]]:
    brutos: list[str] = []
    if MARCADORES_LOCAIS.is_file():
        for linha in MARCADORES_LOCAIS.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#"):
                brutos.append(linha)
    brutos += [parte.strip() for parte in os.environ.get(VARIAVEL_MARCADORES, "").split(";") if parte.strip()]
    padroes = []
    for bruto in brutos:
        marcador = _normalizar(bruto).strip()
        if len(marcador) >= 3:
            padroes.append(re.compile(r"(?<![a-z0-9])" + re.escape(marcador) + r"(?![a-z0-9])"))
    return padroes


def _emails_proibidos(texto: str) -> int:
    return sum(1 for email in EMAIL.findall(texto) if not email.lower().endswith(EMAILS_PERMITIDOS))


def _arquivos_do_repositorio() -> list[Path]:
    """Arquivos versionados e os novos ainda sem commit (os ignorados pelo .gitignore ficam de fora)."""
    try:
        saida = subprocess.run(
            ["git", "-C", str(RAIZ), "-c", "core.quotepath=false", "ls-files", "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
        nomes = [linha for linha in saida.splitlines() if linha.strip()]
    except (OSError, subprocess.CalledProcessError):
        nomes = [str(caminho.relative_to(RAIZ)) for caminho in RAIZ.rglob("*") if caminho.is_file() and ".git" not in caminho.parts]
    arquivos = []
    for nome in sorted(set(nomes)):
        caminho = RAIZ / nome
        if caminho.is_file() and caminho.suffix.lower() not in EXTENSOES_BINARIAS and "screens" not in caminho.parts:
            arquivos.append(caminho)
    return arquivos


def conferir_dados_pessoais(rel: Relatorio) -> None:
    """E-mails, telefones, CPFs e marcadores locais nos arquivos e no histórico do git.

    Imprime só o nome do arquivo e a quantidade, nunca o valor achado.
    """
    marcadores = _marcadores()
    if marcadores:
        print(f"       ({len(marcadores)} marcador(es) pessoal(is) local(is) carregado(s); valores não mostrados)")
    else:
        print(f"       (sem marcadores locais: crie {MARCADORES_LOCAIS.relative_to(RAIZ).as_posix()} ou use {VARIAVEL_MARCADORES})")

    com_email: list[str] = []
    com_numero: list[str] = []
    com_marcador: list[str] = []
    for arquivo in _arquivos_do_repositorio():
        try:
            bruto = arquivo.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        nome = arquivo.relative_to(RAIZ).as_posix()
        if _emails_proibidos(bruto):
            com_email.append(nome)
        # Telefone e CPF só no texto que se lê: números de desenho (SVG) e de código dariam alarme falso.
        if arquivo.suffix.lower() in (".html", ".svg"):
            leitor = TextoVisivel()
            leitor.feed(bruto)
            leitor.close()
            legivel = " ".join(leitor.partes)
        elif arquivo.suffix.lower() in (".md", ".txt"):
            legivel = bruto
        else:
            legivel = ""
        if legivel and (TELEFONE.search(legivel) or CPF.search(legivel)):
            com_numero.append(nome)
        normalizado = _normalizar(bruto)
        if any(padrao.search(normalizado) for padrao in marcadores):
            com_marcador.append(nome)

    rel.conferir(
        "dados pessoais: nenhum e-mail além do privado do GitHub e dos de exemplo",
        not com_email,
        f"arquivos (e-mails não mostrados): {com_email}",
    )
    rel.conferir(
        "dados pessoais: nenhum telefone ou CPF no texto das páginas, imagens e documentos",
        not com_numero,
        f"arquivos (números não mostrados): {com_numero}",
    )
    if marcadores:
        rel.conferir(
            "dados pessoais: nenhum marcador local nos arquivos",
            not com_marcador,
            f"arquivos (marcadores não mostrados): {com_marcador}",
        )

    if not (RAIZ / ".git").exists():
        return
    try:
        historico = subprocess.run(
            ["git", "-C", str(RAIZ), "-c", "core.quotepath=false", "log", "-p", "--no-color", "--no-ext-diff", "--format=%B"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as erro:
        rel.conferir("dados pessoais: histórico do git limpo", False, f"não deu para ler o histórico ({type(erro).__name__})")
        return
    emails = _emails_proibidos(historico)
    normalizado = _normalizar(historico)
    achados = sum(1 for padrao in marcadores if padrao.search(normalizado))
    rel.conferir(
        "dados pessoais: histórico do git (mensagens e alterações) sem e-mail" + (" nem marcador local" if marcadores else ""),
        emails == 0 and achados == 0,
        f"{emails} e-mail(s) e {achados} marcador(es) no histórico (não mostrados)",
    )


# ---------------------------------------------------------------- servidor local


class ServidorDoSite(http.server.SimpleHTTPRequestHandler):
    """Serve o site como o GitHub Pages: em /manda-curriculo/ e com 404.html para o resto."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(RAIZ), **kwargs)

    def log_message(self, *args) -> None:
        pass

    def handle(self) -> None:
        # O navegador às vezes desiste de um pedido no meio (troca de página, contexto fechado).
        # Não é erro do site: sem isto, cada desistência imprimia um traceback na saída.
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_GET(self) -> None:  # noqa: N802 (nome exigido pela biblioteca)
        caminho = unquote(urlsplit(self.path).path)
        if caminho + "/" == PREFIXO:
            caminho = PREFIXO
        if not caminho.startswith(PREFIXO):
            return self._enviar(RAIZ / "404.html", 404)
        relativo = caminho[len(PREFIXO):]
        arquivo = RAIZ / relativo
        if relativo == "" or relativo.endswith("/"):
            arquivo = arquivo / "index.html"
        try:
            arquivo.resolve().relative_to(RAIZ.resolve())
        except ValueError:
            return self._enviar(RAIZ / "404.html", 404)
        if not arquivo.is_file():
            return self._enviar(RAIZ / "404.html", 404)
        return self._enviar(arquivo, 200)

    def _enviar(self, arquivo: Path, codigo: int) -> None:
        corpo = arquivo.read_bytes()
        tipo = self.guess_type(str(arquivo))
        if arquivo.suffix == ".woff2":
            tipo = "font/woff2"
        elif arquivo.suffix == ".webmanifest":
            tipo = "application/manifest+json"
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)


# ---------------------------------------------------------------- navegador


@dataclass
class Sessao:
    navegador: object
    base: str
    origem: str
    externos: list[str] = field(default_factory=list)
    erros_js: list[str] = field(default_factory=list)
    locais_com_erro: list[str] = field(default_factory=list)

    def contexto(
        self,
        largura: int = 1440,
        tema: str = "light",
        api: str = "fora",
        js: bool = True,
        altura: int = 900,
        movimento: str = "reduce",
        toque: bool = False,
        cores_forcadas: bool = False,
        agente: str | None = None,
        so_toque: bool = False,
    ):
        opcoes = {
            "viewport": {"width": largura, "height": altura},
            "color_scheme": tema,
            "reduced_motion": movimento,
            "java_script_enabled": js,
            "locale": "pt-BR",
        }
        if toque:  # celular: tela de toque, sem mouse (pointer: coarse e hover: none)
            opcoes.update(has_touch=True, is_mobile=True)
        if so_toque:  # computador com tela de toque (Windows 2 em 1)
            opcoes["has_touch"] = True
        if agente:
            opcoes["user_agent"] = agente
        if cores_forcadas:  # alto contraste do Windows
            opcoes["forced_colors"] = "active"
        ctx = self.navegador.new_context(**opcoes)
        ctx.route("**/*", self._rota(api))
        return ctx

    def pagina(self, ctx):
        pagina = ctx.new_page()
        pagina.on("pageerror", lambda erro: self.erros_js.append(f"{pagina.url}: {erro}"))
        pagina.on("console", lambda msg: self._console(pagina, msg))
        pagina.on("response", lambda resp: self._resposta(resp))
        return pagina

    def _console(self, pagina, mensagem) -> None:
        # Pedidos bloqueados de propósito (API fora do ar) geram "Failed to load resource";
        # recursos locais quebrados são pegos pelas respostas com erro.
        if mensagem.type == "error" and not mensagem.text.startswith("Failed to load resource"):
            self.erros_js.append(f"{pagina.url}: {mensagem.text}")

    def _resposta(self, resposta) -> None:
        if resposta.url.startswith(self.origem) and resposta.status >= 400:
            if resposta.request.resource_type != "document":
                self.locais_com_erro.append(f"{resposta.status} {resposta.url}")

    def _rota(self, api: str):
        def rota(route) -> None:
            url = route.request.url
            if url.startswith(self.origem):
                route.continue_()
                return
            if url.startswith(API):
                self._api(route, api)
                return
            self.externos.append(url)
            route.abort()

        return rota

    @staticmethod
    def _api(route, modo: str) -> None:
        respostas = {
            "versao": (200, API_VERSAO_NORMAL),
            "pre": (200, API_SO_PRE),
            "vazio": (200, []),
            "404": (404, {"message": "Not Found"}),
            "limite": (403, {"message": "API rate limit exceeded"}),
        }
        if modo not in respostas:
            route.abort()
            return
        codigo, corpo = respostas[modo]
        route.fulfill(status=codigo, content_type="application/json", body=json.dumps(corpo))


def abrir(pagina, url: str):
    resposta = pagina.goto(url, wait_until="load")
    pagina.evaluate("document.fonts.ready.then(() => true)")
    pagina.wait_for_timeout(150)
    return resposta


def conferir_no_navegador(rel: Relatorio, paginas: list[Path], capturas: bool) -> None:
    from playwright.sync_api import sync_playwright

    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ServidorDoSite)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    origem = f"http://127.0.0.1:{servidor.server_address[1]}"
    base = origem + PREFIXO
    if capturas:
        CAPTURAS.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            sessao = Sessao(navegador, base, origem)
            try:
                _rolagem_e_capturas(rel, sessao, paginas, capturas)
                _peso_da_inicial(rel, sessao)
                _estados_do_download(rel, sessao)
                _interacoes(rel, sessao, paginas)
                _foco_e_contraste(rel, sessao, paginas, capturas)
                _contraste_do_texto(rel, sessao, paginas)
                _leitor_de_tela(rel, sessao)
                _celular_e_alto_contraste(rel, sessao, capturas)
                _folha_interna(rel, sessao)
                _pagina_404(rel, sessao)
            finally:
                navegador.close()
            rel.conferir("nenhum erro de JavaScript", not sessao.erros_js, "; ".join(sessao.erros_js[:5]))
            rel.conferir("nenhum recurso local com erro", not sessao.locais_com_erro, "; ".join(sessao.locais_com_erro[:5]))
            rel.conferir(
                "nenhum pedido a outro domínio além da API de versões",
                not sessao.externos,
                "; ".join(sorted(set(sessao.externos))[:5]),
            )
    finally:
        servidor.shutdown()
        servidor.server_close()


def _rolagem_e_capturas(rel: Relatorio, sessao: Sessao, paginas: list[Path], capturas: bool) -> None:
    for tema in TEMAS:
        for largura in LARGURAS_ROLAGEM:
            if tema == "dark" and largura not in LARGURAS_CAPTURA:
                continue
            if tema == "dark" and not capturas:
                continue
            ctx = sessao.contexto(largura=largura, tema=tema)
            pagina = sessao.pagina(ctx)
            try:
                for arquivo in paginas:
                    abrir(pagina, sessao.base + arquivo.name)
                    if tema == "light":
                        medidas = pagina.evaluate(
                            "({s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth})"
                        )
                        rel.conferir(
                            f"{arquivo.name}: sem rolagem horizontal em {largura} px",
                            medidas["s"] <= medidas["c"],
                            f"largura do conteúdo {medidas['s']} > {medidas['c']}",
                        )
                    if capturas and largura in LARGURAS_CAPTURA:
                        destino = CAPTURAS / f"{arquivo.stem}-{largura}-{'claro' if tema == 'light' else 'escuro'}.png"
                        pagina.screenshot(path=str(destino), full_page=True)
            finally:
                ctx.close()


def _peso_da_inicial(rel: Relatorio, sessao: Sessao) -> None:
    ctx = sessao.contexto()
    pagina = sessao.pagina(ctx)
    pedidos: list[str] = []
    pagina.on("request", lambda pedido: pedidos.append(pedido.url))
    try:
        abrir(pagina, sessao.base)
        arquivos = set()
        for url in pedidos:
            caminho = unquote(urlsplit(url).path)
            if url.startswith(sessao.origem) and caminho.startswith(PREFIXO):
                relativo = caminho[len(PREFIXO):] or "index.html"
                arquivos.add(RAIZ / relativo)
        total = sum(arquivo.stat().st_size for arquivo in arquivos if arquivo.is_file())
        rel.conferir(
            "página inicial com menos de 300 KB",
            total < LIMITE_INICIAL,
            f"{total / 1000:.0f} KB em {len(arquivos)} arquivos",
        )
        print(f"       (página inicial: {total / 1000:.0f} KB em {len(arquivos)} arquivos)")
        cookies = ctx.cookies()
        armazenado = pagina.evaluate("localStorage.length + sessionStorage.length")
        rel.conferir("sem cookies nem armazenamento no navegador", not cookies and armazenado == 0)
    finally:
        ctx.close()


def _estado_dos_botoes(pagina) -> list[dict]:
    """Estado de cada bloco de download da página (a inicial tem dois: no topo e no fim)."""
    return pagina.evaluate(
        """() => [...document.querySelectorAll('[data-baixar]')].map((bloco) => {
            const link = bloco.querySelector('[data-baixar-link]');
            const versao = bloco.querySelector('[data-baixar-versao]');
            const reserva = bloco.querySelector('[data-baixar-reserva]');
            const visivel = (el) => Boolean(el) && el.getClientRects().length > 0;
            return {
                estado: bloco.getAttribute('data-estado'),
                href: link.href,
                rotulo: link.textContent.trim().replace(/\\s+/g, ' '),
                versao: visivel(versao) ? versao.textContent.trim() : '',
                reserva: visivel(reserva) ? reserva.textContent.trim().replace(/\\s+/g, ' ') : '',
                reserva_link: visivel(reserva) && reserva.querySelector('a') ? reserva.querySelector('a').href : '',
            };
        })"""
    )


def _reserva_ok(estado: dict) -> bool:
    """Linha de reserva visível, com o link da página de versões e o nome do arquivo a escolher."""
    return bool(estado["reserva"]) and estado["reserva_link"] == PAGINA_VERSOES and ARQUIVO in estado["reserva"]


def _estados_do_download(rel: Relatorio, sessao: Sessao) -> None:
    casos = (
        ("versão publicada", "versao", lambda e: e["estado"] == "pronto" and e["href"] == DOWNLOAD_ULTIMA and "0.1.0" in e["versao"] and "84 MB" in e["versao"] and not e["reserva"]),
        ("só pré-lançamento", "pre", lambda e: e["estado"] == "pronto" and e["href"] == DOWNLOAD_PRE and "0.2.0-beta.1" in e["versao"] and "5,4 MB" in e["versao"] and not e["reserva"]),
        ("sem versão ainda (lista vazia)", "vazio", lambda e: e["href"] == PAGINA_VERSOES and "primeira versão" in e["versao"] and not e["reserva"]),
        ("repositório ainda sem versões (404)", "404", lambda e: e["href"] == PAGINA_VERSOES and "primeira versão" in e["versao"] and not e["reserva"]),
        # API fora do ar ou no limite: o botão continua baixando direto e a reserva diz qual arquivo escolher.
        ("API fora do ar", "fora", lambda e: e["href"] == DOWNLOAD_ULTIMA and e["versao"] == "" and "Baixar" in e["rotulo"] and _reserva_ok(e)),
        ("API com limite de uso", "limite", lambda e: e["href"] == DOWNLOAD_ULTIMA and e["versao"] == "" and "Baixar" in e["rotulo"] and _reserva_ok(e)),
    )
    for nome, modo, esperado in casos:
        ctx = sessao.contexto(api=modo)
        pagina = sessao.pagina(ctx)
        try:
            for endereco in (sessao.base, sessao.base + "baixar.html"):
                abrir(pagina, endereco)
                pagina.wait_for_function("document.querySelector('[data-baixar]').getAttribute('data-estado') !== 'verificando'")
                estados = _estado_dos_botoes(pagina)
                onde = "página inicial" if endereco == sessao.base else "baixar.html"
                rel.conferir(
                    f"download: {nome} ({onde}, {len(estados)} botão(ões))",
                    bool(estados) and all(esperado(estado) for estado in estados),
                    json.dumps(estados, ensure_ascii=False),
                )
            if modo == "versao":
                # O clique mostra como continuar a instalação (sem sair da página no teste).
                abrir(pagina, sessao.base)
                pagina.wait_for_function("document.querySelector('[data-baixar]').getAttribute('data-estado') === 'pronto'")
                pagina.evaluate("document.addEventListener('click', e => e.preventDefault(), true)")
                pagina.click("[data-baixar] [data-baixar-link]")
                visivel = pagina.is_visible("[data-baixar] [data-baixar-depois]")
                rel.conferir("download: depois do clique explica o aviso do navegador e do Windows", visivel)
        finally:
            ctx.close()

    ctx = sessao.contexto(js=False)
    pagina = sessao.pagina(ctx)
    try:
        for endereco in (sessao.base, sessao.base + "baixar.html"):
            abrir(pagina, endereco)
            estados = _estado_dos_botoes(pagina)
            onde = "página inicial" if endereco == sessao.base else "baixar.html"
            rel.conferir(
                f"download: sem JavaScript aponta para o instalador e mostra a reserva ({onde})",
                bool(estados) and all(e["href"] == DOWNLOAD_ULTIMA and _reserva_ok(e) for e in estados),
                json.dumps(estados, ensure_ascii=False),
            )
    finally:
        ctx.close()


def _interacoes(rel: Relatorio, sessao: Sessao, paginas: list[Path]) -> None:
    ctx = sessao.contexto()
    pagina = sessao.pagina(ctx)
    try:
        abrir(pagina, sessao.base)

        pagina.keyboard.press("Tab")
        primeiro = pagina.evaluate("document.activeElement.textContent.trim()")
        rel.conferir("teclado: primeiro Tab vai para Pular para o conteúdo", primeiro == "Pular para o conteúdo", primeiro)
        chegou = False
        for _ in range(12):
            if pagina.evaluate("document.activeElement.matches('[data-baixar-link]')"):
                chegou = True
                break
            pagina.keyboard.press("Tab")
        rel.conferir("teclado: botão de download alcançado em poucos Tabs", chegou)
        contorno = pagina.evaluate(
            "(() => { const s = getComputedStyle(document.activeElement); return s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2; })()"
        )
        rel.conferir("teclado: foco visível no botão de download", contorno)

        pagina.click("button[data-area='enfermagem']")
        troca = pagina.evaluate(
            """() => ({
                visivel: [...document.querySelectorAll('[data-lista]')].filter(b => !b.hidden).map(b => b.dataset.lista),
                marcado: document.querySelector("button[data-area='enfermagem']").getAttribute('aria-pressed'),
                rotulo: document.querySelector('[data-folha-area]').textContent.trim(),
            })"""
        )
        rel.conferir(
            "exemplo: trocar a área mostra só a área escolhida",
            troca == {"visivel": ["enfermagem"], "marcado": "true", "rotulo": "Enfermagem"},
            json.dumps(troca, ensure_ascii=False),
        )

        pagina.focus("details.pergunta summary")
        pagina.keyboard.press("Enter")
        aberta = pagina.evaluate("document.querySelector('details.pergunta').open")
        pagina.keyboard.press("Enter")
        fechada = not pagina.evaluate("document.querySelector('details.pergunta').open")
        rel.conferir("perguntas frequentes: abrem e fecham pelo teclado", aberta and fechada)

        for arquivo in paginas:
            abrir(pagina, sessao.base + arquivo.name)
            sem_nome = pagina.evaluate(
                """() => [...document.querySelectorAll('a[href], button, summary')]
                    .filter(el => el.getClientRects().length > 0)
                    .filter(el => !(el.getAttribute('aria-label') || el.textContent || '').trim())
                    .map(el => el.outerHTML.slice(0, 80))"""
            )
            rel.conferir(f"{arquivo.name}: links e botões com nome", not sem_nome, f"sem nome: {sem_nome}")
    finally:
        ctx.close()


def _aneis_de_foco(pagina, seletor: str) -> list[dict]:
    """Foca cada elemento visível do seletor e mede o anel de foco contra o fundo atrás dele.

    Nos botões de download, o anel passa também por cima da passada amarela de marca-texto
    (.marcado::before) nas bordas de baixo e da direita: aí ele é medido contra a cor da
    passada misturada ao fundo (com a opacidade que ela tem durante o foco), e vale o menor
    dos dois contrastes.
    """
    # Um Tab antes: depois de uma tecla, o foco por script também conta como foco de teclado.
    pagina.keyboard.press("Tab")
    return pagina.evaluate(
        f"""(seletor) => {{
            const mc = {JS_CORES};
            // Abre as perguntas frequentes, para medir também os links das respostas.
            document.querySelectorAll('details').forEach((detalhe) => {{ detalhe.open = true; }});
            return [...document.querySelectorAll(seletor)]
                .filter((el) => el.getClientRects().length > 0)
                .map((el) => {{
                    el.focus();
                    const s = getComputedStyle(el);
                    const anel = mc.cor(s.outlineColor);
                    let contraste = anel ? mc.contraste(anel, mc.fundo(el.parentElement)) : 0;
                    let passada = null;
                    const marcado = el.closest('.marcado');
                    if (anel && marcado) {{
                        const sp = getComputedStyle(marcado, '::before');
                        const cor = mc.cor(sp.backgroundColor);
                        if (sp.content !== 'none' && cor && cor[3] > 0) {{
                            const alfa = cor[3] * parseFloat(sp.opacity);
                            const atras = mc.fundo(marcado.parentElement);
                            const mistura = [0, 1, 2].map((i) => cor[i] * alfa + atras[i] * (1 - alfa)).concat(1);
                            passada = mc.contraste(anel, mistura);
                            contraste = Math.min(contraste, passada);
                        }}
                    }}
                    return {{
                        nome: mc.nome(el),
                        visivel: el.matches(':focus-visible'),
                        tracado: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) >= 2,
                        contraste,
                        passada,
                    }};
                }});
        }}""",
        seletor,
    )


def _foco_e_contraste(rel: Relatorio, sessao: Sessao, paginas: list[Path], capturas: bool) -> None:
    for tema in TEMAS:
        nome_tema = "claro" if tema == "light" else "escuro"
        ctx = sessao.contexto(tema=tema, api="versao")
        pagina = sessao.pagina(ctx)
        try:
            # Todo link e botão de todas as páginas: anel de foco visível e com contraste contra o fundo.
            for arquivo in paginas:
                abrir(pagina, sessao.base + arquivo.name)
                aneis = _aneis_de_foco(pagina, FOCAVEIS)
                ruins = [a for a in aneis if not (a["visivel"] and a["tracado"] and a["contraste"] >= CONTRASTE_FOCO)]
                rel.conferir(
                    f"{arquivo.name}: anel de foco visível em {len(aneis)} links e botões ({nome_tema})",
                    bool(aneis) and not ruins,
                    json.dumps(ruins[:4], ensure_ascii=False),
                )

            # Todos os botões de download, inclusive o da faixa azul do fim da página inicial.
            for endereco in (sessao.base, sessao.base + "baixar.html"):
                abrir(pagina, endereco)
                pagina.wait_for_function("document.querySelector('[data-baixar]').getAttribute('data-estado') === 'pronto'")
                aneis = _aneis_de_foco(pagina, "[data-baixar-link]")
                rel.conferir(
                    f"download: anel de foco com contraste no fundo e na passada amarela, em todos os {len(aneis)} botões ({nome_tema}, {endereco.rsplit('/', 1)[-1] or 'index.html'})",
                    bool(aneis) and all(
                        a["visivel"] and a["tracado"] and a["passada"] is not None and a["contraste"] >= CONTRASTE_FOCO
                        for a in aneis
                    ),
                    json.dumps(aneis, ensure_ascii=False),
                )

            # Depois do clique em baixar aparece a caixa clara com o link do passo a passo:
            # o texto e o foco do link precisam de contraste (na faixa azul ele chegou a ficar branco).
            abrir(pagina, sessao.base)
            pagina.wait_for_function("document.querySelector('[data-baixar]').getAttribute('data-estado') === 'pronto'")
            pagina.evaluate("document.addEventListener('click', e => e.preventDefault(), true)")
            for link in pagina.query_selector_all("[data-baixar-link]"):
                link.click()
            links = pagina.evaluate(
                f"""() => {{
                    const mc = {JS_CORES};
                    return [...document.querySelectorAll('[data-baixar-depois] a')]
                        .filter((a) => a.getClientRects().length > 0)
                        .map((a) => ({{
                            nome: mc.nome(a),
                            contraste: mc.contraste(mc.cor(getComputedStyle(a).color), mc.fundo(a)),
                        }}));
                }}"""
            )
            rel.conferir(
                f"download: links depois do clique legíveis ({nome_tema})",
                len(links) == 2 and all(link["contraste"] >= CONTRASTE_TEXTO for link in links),
                json.dumps(links, ensure_ascii=False),
            )
            aneis = _aneis_de_foco(pagina, "[data-baixar-depois] a")
            rel.conferir(
                f"download: foco visível nos links depois do clique ({nome_tema})",
                len(aneis) == 2 and all(a["visivel"] and a["tracado"] and a["contraste"] >= CONTRASTE_FOCO for a in aneis),
                json.dumps(aneis, ensure_ascii=False),
            )
            if capturas:
                faixa = pagina.locator(".faixa-forte")
                pagina.focus(".faixa-forte [data-baixar-depois] a")
                faixa.screenshot(path=str(CAPTURAS / f"index-faixa-final-{nome_tema}-depois-do-clique.png"))
                pagina.focus(".faixa-forte [data-baixar-link]")
                faixa.screenshot(path=str(CAPTURAS / f"index-faixa-final-{nome_tema}-foco.png"))
        finally:
            ctx.close()


def _leitor_de_tela(rel: Relatorio, sessao: Sessao) -> None:
    ctx = sessao.contexto()
    pagina = sessao.pagina(ctx)
    try:
        abrir(pagina, sessao.base)
        # "O que ele nunca faz": o risco não é lido pelo leitor de tela, então o "Nunca:" precisa
        # estar no nome do título; senão a pessoa ouve o contrário da promessa.
        titulos = pagina.locator(".nunca h3")
        com_nunca = pagina.locator(".nunca").get_by_role("heading", level=3, name=re.compile(r"^Nunca:"))
        total, certos = titulos.count(), com_nunca.count()
        rel.conferir(
            "leitor de tela: os títulos de “O que ele nunca faz” começam com “Nunca:”",
            total >= 5 and certos == total,
            f"{certos} de {total}: {titulos.all_text_contents()}",
        )
        # Na tela, cada título também mostra o rótulo "Nunca": o risco sozinho podia ser lido
        # como recurso ("Inventar coisas no seu currículo").
        rotulos = pagina.evaluate(
            """() => [...document.querySelectorAll('.nunca h3')].map((titulo) => {
                const rotulo = titulo.querySelector('.nunca-rotulo');
                if (!rotulo || rotulo.closest('.so-leitor')) return false;
                const caixa = rotulo.getBoundingClientRect();
                return caixa.width > 20 && caixa.height > 10 && rotulo.innerText.trim().startsWith('Nunca');
            })"""
        )
        rel.conferir(
            "“O que ele nunca faz”: rótulo “Nunca” visível em todos os títulos",
            len(rotulos) >= 5 and all(rotulos),
            str(rotulos),
        )

        # Rótulos em inglês (inclusive "Issues") marcados com lang="en", para o leitor de tela
        # pronunciar certo.
        for arquivo in sorted(RAIZ.glob("*.html")):
            abrir(pagina, sessao.base + arquivo.name)
            sem_lang = pagina.evaluate(
                """(termos) => {
                    const achados = [];
                    const andar = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                    for (let no = andar.nextNode(); no; no = andar.nextNode()) {
                        for (const termo of termos) {
                            if (no.textContent.includes(termo) && !no.parentElement.closest('[lang="en"]')) {
                                achados.push(termo);
                            }
                        }
                    }
                    return achados;
                }""",
                list(TERMOS_EM_INGLES),
            )
            rel.conferir(f"{arquivo.name}: rótulos em inglês com lang=\"en\"", not sem_lang, f"sem lang: {sem_lang}")
    finally:
        ctx.close()


AVISO_DO_CELULAR = """() => {
    const bloco = document.querySelector('[data-baixar]');
    const aviso = bloco.querySelector('.baixar-celular');
    const botao = bloco.querySelector('[data-baixar-link]');
    const a = aviso.getBoundingClientRect();
    const b = botao.getBoundingClientRect();
    return {
        visivel: a.height > 0,
        antes_na_tela: a.height > 0 && a.bottom <= b.top,
        antes_no_html: Boolean(aviso.compareDocumentPosition(botao) & Node.DOCUMENT_POSITION_FOLLOWING),
        rolagem: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    };
}"""


def _celular_e_alto_contraste(rel: Relatorio, sessao: Sessao, capturas: bool) -> None:
    # Celular de verdade (Android, toque, sem mouse): o aviso "Está no celular?" aparece antes do
    # botão, na tela e no HTML (a ordem do teclado e do leitor de tela é a mesma da tela).
    ctx = sessao.contexto(largura=360, altura=740, toque=True, agente=AGENTE_ANDROID)
    pagina = sessao.pagina(ctx)
    try:
        for arquivo in ("index.html", "baixar.html"):
            abrir(pagina, sessao.base + arquivo)
            medidas = pagina.evaluate(AVISO_DO_CELULAR)
            rel.conferir(
                f"{arquivo}: no celular, “Está no celular?” aparece antes do botão de download (na tela e no HTML)",
                medidas["visivel"] and medidas["antes_na_tela"] and medidas["antes_no_html"] and medidas["rolagem"],
                json.dumps(medidas),
            )
            if arquivo == "baixar.html":
                fontes = pagina.evaluate("() => [...document.querySelectorAll('.figura img')].map((img) => img.currentSrc.split('/').pop())")
                rel.conferir(
                    "baixar.html: no celular, as ilustrações usam a versão para tela estreita",
                    bool(fontes) and all(fonte.endswith("-celular.svg") for fonte in fontes),
                    str(fontes),
                )
            if capturas:
                # Sem full_page: a captura de página inteira troca a emulação do celular e o
                # navegador deixa de se comportar como tela de toque. Aqui a janela cresce até
                # caber a página toda e depois volta ao tamanho de celular.
                altura = pagina.evaluate("document.documentElement.scrollHeight")
                pagina.set_viewport_size({"width": 360, "height": min(altura, 12000)})
                pagina.screenshot(path=str(CAPTURAS / f"{Path(arquivo).stem}-360-celular.png"))
                pagina.set_viewport_size({"width": 360, "height": 740})
    finally:
        ctx.close()

    # No computador o aviso nunca aparece: nem com zoom de 200% (janela de 640 px), nem no
    # Windows com tela de toque, nem sem JavaScript (no celular sem JavaScript ele também some).
    casos = (
        ("computador com zoom de 200%", {"largura": 640}),
        ("Windows com tela de toque", {"largura": 1024, "altura": 700, "so_toque": True, "agente": AGENTE_WINDOWS}),
        ("sem JavaScript", {"largura": 360, "altura": 740, "js": False}),
    )
    for nome, opcoes in casos:
        ctx = sessao.contexto(**opcoes)
        pagina = sessao.pagina(ctx)
        try:
            for arquivo in ("index.html", "baixar.html"):
                abrir(pagina, sessao.base + arquivo)
                medidas = pagina.evaluate(AVISO_DO_CELULAR)
                rel.conferir(
                    f"{arquivo}: “Está no celular?” escondido ({nome})",
                    not medidas["visivel"] and medidas["rolagem"],
                    json.dumps(medidas),
                )
        finally:
            ctx.close()

    # Tela larga: as ilustrações grandes.
    ctx = sessao.contexto()
    pagina = sessao.pagina(ctx)
    try:
        abrir(pagina, sessao.base + "baixar.html")
        fontes = pagina.evaluate("() => [...document.querySelectorAll('.figura img')].map((img) => img.currentSrc.split('/').pop())")
        rel.conferir(
            "baixar.html: em tela larga, as ilustrações usam a versão grande",
            bool(fontes) and not any(fonte.endswith("-celular.svg") for fonte in fontes),
            str(fontes),
        )
    finally:
        ctx.close()

    # Tema escuro com animação ligada: o grifo aparece pronto (sem a passada, o texto escuro
    # sumia na folha escura); no tema claro, a passada continua.
    for tema, esperado in (("light", "mc-passada"), ("dark", "none")):
        ctx = sessao.contexto(tema=tema, movimento="no-preference")
        pagina = sessao.pagina(ctx)
        try:
            abrir(pagina, sessao.base)
            animacao = pagina.evaluate("getComputedStyle(document.querySelector('[data-lista]:not([hidden]) .grifo')).animationName")
            rel.conferir(f"exemplo: grifo {'sem' if esperado == 'none' else 'com'} animação no tema {'escuro' if tema == 'dark' else 'claro'}", animacao == esperado, animacao)
        finally:
            ctx.close()

    # Alto contraste do Windows (tema escuro): grifo, botão e +/− das perguntas continuam visíveis.
    ctx = sessao.contexto(largura=768, tema="dark", cores_forcadas=True)
    pagina = sessao.pagina(ctx)
    try:
        abrir(pagina, sessao.base)
        medidas = pagina.evaluate(
            f"""() => {{
                const mc = {JS_CORES};
                const grifo = document.querySelector('[data-lista]:not([hidden]) .grifo');
                const sg = getComputedStyle(grifo);
                const fundoGrifo = mc.cor(sg.backgroundColor);
                const botao = getComputedStyle(document.querySelector('[data-baixar-link]'));
                const resumo = document.querySelector('.pergunta summary');
                const icone = mc.cor(getComputedStyle(resumo, '::before').backgroundColor);
                return {{
                    grifo: fundoGrifo && fundoGrifo[3] > 0 ? mc.contraste(mc.cor(sg.color), fundoGrifo) : 0,
                    borda_do_botao: botao.borderTopStyle !== 'none' && parseFloat(botao.borderTopWidth) >= 1,
                    sinal_da_pergunta: icone && icone[3] > 0 ? mc.contraste(icone, mc.fundo(resumo)) : 0,
                }};
            }}"""
        )
        rel.conferir(
            "alto contraste: grifo, botão de download e +/− das perguntas visíveis",
            medidas["grifo"] >= CONTRASTE_TEXTO and medidas["borda_do_botao"] and medidas["sinal_da_pergunta"] >= CONTRASTE_FOCO,
            json.dumps(medidas),
        )
        if capturas:
            pagina.screenshot(path=str(CAPTURAS / "index-768-alto-contraste.png"), full_page=True)
    finally:
        ctx.close()


def _pagina_404(rel: Relatorio, sessao: Sessao) -> None:
    ctx = sessao.contexto(largura=320)
    pagina = sessao.pagina(ctx)
    try:
        resposta = abrir(pagina, sessao.base + "uma/pasta/que-nao-existe")
        titulo = pagina.text_content("h1") or ""
        com_estilo = pagina.evaluate("getComputedStyle(document.body).display === 'flex'")
        rel.conferir(
            "404: endereço inexistente em qualquer pasta mostra a página com estilo",
            resposta is not None and resposta.status == 404 and "não existe" in titulo and com_estilo,
            f"status {resposta.status if resposta else None}, título {titulo!r}, estilo {com_estilo}",
        )
        destino = pagina.evaluate("document.querySelector('.saidas a').href")
        rel.conferir("404: link para a página inicial aponta para o site", destino == sessao.base, destino)

        # Pelo teclado: Tab vai para "Pular para o conteúdo" e Enter fica na própria 404
        # (com <base>, o link levava à página inicial).
        antes = urlsplit(pagina.url)
        pagina.keyboard.press("Tab")
        no_pular = pagina.evaluate("document.activeElement.classList.contains('pular')")
        pagina.keyboard.press("Enter")
        pagina.wait_for_timeout(300)
        pagina.wait_for_load_state("load")
        depois = urlsplit(pagina.url)
        rel.conferir(
            "404: “Pular para o conteúdo” fica na própria página",
            no_pular and depois.path == antes.path and depois.fragment == "conteudo",
            f"foco no link: {no_pular}; foi para {depois.path}#{depois.fragment}",
        )
    finally:
        ctx.close()


def _contraste_do_texto(rel: Relatorio, sessao: Sessao, paginas: list[Path]) -> None:
    """Todo texto visível de todas as páginas, nos dois temas: 4,5:1 (3:1 para texto grande).

    Exceção conhecida: o grifo (.grifo), com fundo amarelo em degradê, que o cálculo pela cor de
    fundo não enxerga; o texto dele é escuro sobre o amarelo nos dois temas e é conferido no
    alto contraste.
    """
    for tema in TEMAS:
        nome_tema = "claro" if tema == "light" else "escuro"
        ctx = sessao.contexto(tema=tema, api="versao")
        pagina = sessao.pagina(ctx)
        try:
            for arquivo in paginas:
                abrir(pagina, sessao.base + arquivo.name)
                resultado = pagina.evaluate(
                    f"""() => {{
                        const mc = {JS_CORES};
                        document.querySelectorAll('details').forEach((detalhe) => {{ detalhe.open = true; }});
                        const ruins = [];
                        const vistos = new Set();
                        let medidos = 0;
                        const andar = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                        for (let no = andar.nextNode(); no; no = andar.nextNode()) {{
                            const el = no.parentElement;
                            if (!el || vistos.has(el) || !no.textContent.trim()) continue;
                            vistos.add(el);
                            if (el.closest('.grifo, .so-leitor, svg')) continue;
                            const s = getComputedStyle(el);
                            if (s.visibility !== 'visible' || el.getClientRects().length === 0) continue;
                            const cor = mc.cor(s.color);
                            const tamanho = parseFloat(s.fontSize);
                            const grande = tamanho >= 24 || (tamanho >= 18.66 && parseInt(s.fontWeight, 10) >= 700);
                            const minimo = grande ? 3 : 4.5;
                            const contraste = cor ? mc.contraste(cor, mc.fundo(el)) : 0;
                            medidos += 1;
                            if (contraste < minimo) {{
                                ruins.push({{ texto: no.textContent.trim().replace(/\\s+/g, ' ').slice(0, 40), contraste, minimo }});
                            }}
                        }}
                        return {{ medidos, ruins }};
                    }}"""
                )
                rel.conferir(
                    f"{arquivo.name}: contraste de todo o texto ({resultado['medidos']} trechos, {nome_tema})",
                    resultado["medidos"] > 0 and not resultado["ruins"],
                    json.dumps(resultado["ruins"][:5], ensure_ascii=False),
                )
        finally:
            ctx.close()


def _folha_interna(rel: Relatorio, sessao: Sessao) -> None:
    """Páginas internas em tela larga: anotações (índice ou caixa de download) na margem, à
    esquerda da margem vermelha, acompanhando a rolagem; o texto ao lado, sem sobrepor."""
    ctx = sessao.contexto(largura=1440, api="versao")
    pagina = sessao.pagina(ctx)
    try:
        for nome in ("ajuda.html", "baixar.html", "privacidade.html", "termos.html"):
            abrir(pagina, sessao.base + nome)
            medidas = pagina.evaluate(
                """() => {
                    const notas = document.querySelector('.folha-interna > .margem-notas');
                    const prosa = document.querySelector('.folha-interna > .prosa');
                    if (!notas || !prosa) return null;
                    const a = notas.getBoundingClientRect();
                    const b = prosa.getBoundingClientRect();
                    return {
                        lado_a_lado: a.width > 0 && a.right <= b.left && a.top < b.bottom,
                        fixa: getComputedStyle(notas).position === 'sticky',
                        margem_vermelha: parseFloat(getComputedStyle(prosa).borderLeftWidth) >= 1,
                    };
                }"""
            )
            rel.conferir(
                f"{nome}: em tela larga, anotações na margem e texto ao lado",
                bool(medidas) and all(medidas.values()),
                json.dumps(medidas),
            )
            pagina.evaluate("window.scrollTo(0, document.documentElement.scrollHeight * 0.6)")
            pagina.wait_for_timeout(100)
            acompanha = pagina.evaluate(
                """() => {
                    const notas = document.querySelector('.folha-interna > .margem-notas');
                    if (!notas) return false;
                    const r = notas.getBoundingClientRect();
                    return r.top >= 0 && r.bottom <= innerHeight;
                }"""
            )
            rel.conferir(f"{nome}: as anotações da margem acompanham a rolagem", acompanha)
    finally:
        ctx.close()


# ---------------------------------------------------------------- principal


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argumentos = argparse.ArgumentParser(description="Verificação automática do site do Manda Currículo.")
    argumentos.add_argument("--sem-capturas", action="store_true", help="não gera as capturas em tools/screens/")
    opcoes = argumentos.parse_args(argv)

    paginas = sorted(RAIZ.glob("*.html"))
    rel = Relatorio()
    print(f"Site em {RAIZ} ({len(paginas)} páginas)\n")

    conferir_paginas(rel, paginas)
    conferir_css_e_js(rel)
    conferir_arquivos(rel, paginas)
    conferir_ilustracoes(rel)
    conferir_git(rel)
    conferir_dados_pessoais(rel)
    conferir_no_navegador(rel, paginas, capturas=not opcoes.sem_capturas)

    total = rel.passaram + len(rel.falhas)
    print(f"\n{rel.passaram} de {total} verificações passaram.")
    if not opcoes.sem_capturas:
        print(f"Capturas em {CAPTURAS}")
    if rel.falhas:
        print("\nFALHOU:")
        for falha in rel.falhas:
            print(f"  - {falha}")
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
