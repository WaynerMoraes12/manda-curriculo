# Manda Currículo

Programa gratuito para Windows que procura vagas de emprego e se candidata por você, todo dia, com o
currículo ajustado para cada vaga e sem inventar nada. Seus dados ficam no seu computador.

- **Site:** https://waynermoraes12.github.io/manda-curriculo/
- **Baixar:** [página de versões](https://github.com/WaynerMoraes12/manda-curriculo/releases)
  (instalador `MandaCurriculo-win-Setup.exe`, para Windows 10 e 11)
- **Dúvidas, problemas e sugestões:** [Issues](https://github.com/WaynerMoraes12/manda-curriculo/issues).
  As Issues são públicas: nunca coloque nelas CPF, telefone, e-mail, currículo ou chave de IA.
- **Problema de segurança:** não abra Issue. Na aba
  [Security](https://github.com/WaynerMoraes12/manda-curriculo/security), use "Report a vulnerability": o aviso
  é particular e só quem mantém o projeto vê (veja `SECURITY.md`).

Versão de teste (beta). Projeto independente e gratuito, sem ligação com a Gupy, o LinkedIn, o Google, a
Microsoft ou outras empresas citadas.

## O que tem neste repositório

O site, em HTML, CSS e JavaScript escritos à mão (sem framework e sem etapa de build), publicado pelo
GitHub Pages a partir da raiz da branch `main`, e as versões do instalador (Releases).

| Arquivo | Para quê |
|---|---|
| `index.html`, `baixar.html`, `ajuda.html`, `privacidade.html`, `termos.html`, `404.html` | páginas do site |
| `assets/css/tokens.css` | cores, tipografia, espaços, raios, sombras e modo escuro; o painel do programa usa uma cópia |
| `assets/css/fonts.css` e `assets/fonts/` | fonte Atkinson Hyperlegible Next (SIL Open Font License 1.1, em `assets/fonts/OFL.txt`) |
| `assets/css/site.css`, `assets/js/site.js` | estilos e o pouco de JavaScript do site |
| `status.json` | aviso remoto lido pelo programa (veja abaixo) |
| `SECURITY.md` | como avisar um problema de segurança sem expor ninguém |
| `tools/` | verificação automática e geração das imagens |

O site não usa cookies, rastreadores, nem fontes ou scripts de outros domínios. A única consulta externa é
à API pública de versões do GitHub, para mostrar a versão e o tamanho do instalador.

## `status.json`

O programa lê este arquivo a cada ciclo. Ele **só pode desligar** coisas (pausar tudo, pausar uma fonte de
vagas ou uma plataforma, exigir uma versão mínima, mostrar um aviso), nunca ligar nem mandar código.
Formato:

```json
{"pausar_tudo": false, "fontes_pausadas": [], "plataformas_pausadas": [], "versao_minima": "0.1.0", "aviso": ""}
```

## Verificar o site

Com um Python que tenha o Playwright e o Chromium dele instalados:

```
python tools/check_site.py                 # tudo, com capturas em tools/screens/
python tools/check_site.py --sem-capturas  # mais rápido
python tools/gerar_imagens.py              # refaz og.png e os ícones a partir dos SVGs
```

O `check_site.py` serve o site em 127.0.0.1 no mesmo caminho do GitHub Pages (`/manda-curriculo/`), simula a
API de versões e não acessa a internet. Ele confere links internos (inclusive links só de fragmento numa
página com `<base>`), títulos e descrições, recursos de outros domínios, `status.json`, palavras que não
podem aparecer, rolagem horizontal de 320 a 1440 px, o peso da página inicial (menos de 300 KB), o botão
de download em todos os estados da API (inclusive a linha de reserva quando a API falha), o uso pelo
teclado (também o "Pular para o conteúdo" da 404), o contraste de todo texto e do anel de foco em todos os
links e botões, nos dois temas (nos botões de download, também por cima da passada amarela), o aviso "Está
no celular?" (só em celular de verdade: nunca no computador com zoom, no Windows com tela de toque ou sem
JavaScript), a folha das páginas internas em tela larga, o nome lido pelo leitor de tela (lista "O que ele
nunca faz" e rótulos em inglês, como Issues, com `lang="en"`) e se todos os commits usam o e-mail privado
do GitHub (`@users.noreply.github.com`), sem mostrar nenhum e-mail na saída. As capturas (claro e escuro,
320, 768 e 1440 px, mais o celular com toque e o alto contraste do Windows) ficam em `tools/screens/`, fora
do git, para revisão visual.

Dados pessoais: o script procura e-mails (fora o privado do GitHub e os de exemplo), telefones e CPFs nos
arquivos e no histórico do git. Para procurar também nome, sobrenome, cidade, empregadores e o que mais
não pode aparecer, escreva um marcador por linha em `tools/marcadores.local.txt` (fica fora do git) ou
passe-os na variável `MANDA_MARCADORES`, separados por `;`. A saída mostra só o nome do arquivo, nunca o
que foi achado.

## Publicar o site e a primeira versão

Siga esta ordem: o botão de download aponta para `releases/latest/download/MandaCurriculo-win-Setup.exe`,
que dá página não encontrada enquanto não existir uma versão normal (não pré-lançamento). Quando a API do
GitHub falha, está no limite (60 consultas por hora por endereço IP, e muita gente divide o mesmo IP na
internet do celular) ou o JavaScript está desligado, o site não consegue saber se já existe versão e o
botão continua nesse endereço.

1. No GitHub, deixe ligadas "Keep my email addresses private" e "Block command line pushes that expose my
   email" antes do primeiro `git push`.
2. Ligue "Private vulnerability reporting" nas configurações do repositório: a ajuda, a política de
   privacidade e o `SECURITY.md` mandam usar "Report a vulnerability".
3. Publique a primeira Release como versão normal (não pré-lançamento), com o arquivo
   `MandaCurriculo-win-Setup.exe`.
4. Só então ligue o GitHub Pages (branch `main`, pasta raiz) e divulgue o endereço.

Depois disso, cada versão nova é uma Release com o mesmo nome de arquivo. Se a versão for marcada como
pré-lançamento, o site usa o endereço do arquivo dessa versão. Sem nenhuma versão publicada, o botão leva à
página de versões e o site avisa que a primeira versão está chegando. Se a API do GitHub não responder, o
botão continua apontando para o instalador e aparece a linha "Se o download não começar, abra a página de
versões e clique em MandaCurriculo-win-Setup.exe"; sem JavaScript, essa linha já aparece de saída.

Domínio próprio: a `404.html` usa caminhos a partir de `/manda-curriculo/` (o GitHub Pages a mostra em
qualquer endereço que não existe). Se o site mudar de endereço, troque esse caminho junto com `canonical`,
`og:url`, `sitemap.xml` e `robots.txt`.
