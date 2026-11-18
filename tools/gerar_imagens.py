"""Gera as imagens do site a partir dos SVGs próprios.

- assets/img/og.png (1200x630, imagem de compartilhamento) a partir de tools/og.svg;
- assets/img/icone-180.png (ícone da tela inicial do iPhone, sem cantos transparentes),
  icone-192.png e icone-512.png (manifesto) a partir de favicon.svg.

Uso: python tools/gerar_imagens.py
Precisa de um Python com o Playwright e o Chromium dele instalados. Não usa a internet:
o site é servido só em 127.0.0.1 enquanto o script roda.
"""

from __future__ import annotations

import functools
import http.server
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parents[1]
SAIDA = RAIZ / "assets" / "img"

# (tamanho em px, arquivo, quadrado cheio sem cantos arredondados)
ICONES = (
    (180, "icone-180.png", True),
    (192, "icone-192.png", False),
    (512, "icone-512.png", False),
)


class _SemLog(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # silencia o log de cada pedido
        pass


def _somente_local(rota) -> None:
    if rota.request.url.startswith("http://127.0.0.1"):
        rota.continue_()
    else:
        rota.abort()


def main() -> int:
    manipulador = functools.partial(_SemLog, directory=str(RAIZ))
    servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), manipulador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{servidor.server_address[1]}/"
    SAIDA.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            navegador = p.chromium.launch()
            try:
                contexto = navegador.new_context(viewport={"width": 1200, "height": 630})
                contexto.route("**/*", _somente_local)
                pagina = contexto.new_page()

                pagina.goto(base + "tools/og.svg")
                pagina.evaluate("document.fonts.ready.then(() => true)")
                pagina.wait_for_timeout(200)
                pagina.screenshot(path=str(SAIDA / "og.png"))
                print("assets/img/og.png")

                svg = (RAIZ / "favicon.svg").read_text(encoding="utf-8")
                pagina.goto("about:blank")  # set_content só funciona sobre documento HTML
                for tamanho, nome, cheio in ICONES:
                    marcacao = svg.replace(
                        "<svg ", f'<svg style="display:block" width="{tamanho}" height="{tamanho}" ', 1
                    )
                    if cheio:
                        marcacao = marcacao.replace('rx="15"', 'rx="0"', 1)
                    pagina.set_viewport_size({"width": tamanho, "height": tamanho})
                    pagina.set_content(
                        "<!doctype html><html><body style='margin:0;background:transparent'>"
                        f"{marcacao}</body></html>"
                    )
                    pagina.screenshot(
                        path=str(SAIDA / nome),
                        omit_background=not cheio,
                        clip={"x": 0, "y": 0, "width": tamanho, "height": tamanho},
                    )
                    print(f"assets/img/{nome}")
            finally:
                navegador.close()
    finally:
        servidor.shutdown()
        servidor.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
