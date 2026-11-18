/*
  Manda Currículo: o pouco de JavaScript do site.
  - Mostra a versão e o tamanho do instalador, consultando a API pública de versões
    do GitHub direto do navegador. Enquanto a API não responde, ou se ela falhar, o
    botão continua baixando o instalador direto e aparece a linha de reserva ("se o
    download não começar, abra a página de versões"). Só quando a API confirma que
    ainda não há versão o botão leva à página de versões.
  - Troca a área do "dia de exemplo".
  - No celular, ajuda a mandar o link do site para abrir no computador.
  Sem cookies, sem armazenamento e sem rastreadores.
*/
(function () {
  "use strict";

  var REPO = "WaynerMoraes12/manda-curriculo";
  var ARQUIVO = "MandaCurriculo-win-Setup.exe";
  var PAGINA_VERSOES = "https://github.com/" + REPO + "/releases";
  var DOWNLOAD_ULTIMA = "https://github.com/" + REPO + "/releases/latest/download/" + ARQUIVO;
  var API_VERSOES = "https://api.github.com/repos/" + REPO + "/releases?per_page=10";
  var ROTULO_BAIXAR = "Baixar grátis para Windows";
  var ESPERA_MAXIMA_MS = 8000;

  function todos(seletor, raiz) {
    return Array.prototype.slice.call((raiz || document).querySelectorAll(seletor));
  }

  /* ---------- Versão e download ---------- */

  function tamanhoLegivel(bytes) {
    if (typeof bytes !== "number" || !(bytes > 0)) {
      return "";
    }
    var mb = bytes / (1024 * 1024);
    var casas = mb < 10 ? 1 : 0;
    return mb.toLocaleString("pt-BR", { maximumFractionDigits: casas, minimumFractionDigits: 0 }) + " MB";
  }

  function enderecoDoGithub(url) {
    return typeof url === "string" && url.indexOf("https://github.com/") === 0;
  }

  /* Escolhe a versão mais nova que tenha o instalador. Versão normal usa o endereço
     fixo de "última versão"; versão de teste marcada como pré-lançamento usa o
     endereço do próprio arquivo. */
  function escolherVersao(lista) {
    if (!Array.isArray(lista)) {
      return null;
    }
    for (var i = 0; i < lista.length; i++) {
      var versao = lista[i];
      if (!versao || versao.draft) {
        continue;
      }
      var arquivos = Array.isArray(versao.assets) ? versao.assets : [];
      for (var j = 0; j < arquivos.length; j++) {
        var arquivo = arquivos[j];
        if (!arquivo || arquivo.name !== ARQUIVO) {
          continue;
        }
        var url = versao.prerelease ? arquivo.browser_download_url : DOWNLOAD_ULTIMA;
        if (!enderecoDoGithub(url)) {
          continue;
        }
        return {
          tipo: "pronto",
          versao: String(versao.tag_name || "").replace(/^v/i, ""),
          tamanho: tamanhoLegivel(arquivo.size),
          url: url
        };
      }
    }
    return null;
  }

  function aplicarEstado(estado) {
    todos("[data-baixar]").forEach(function (bloco) {
      var link = bloco.querySelector("[data-baixar-link]");
      var rotulo = bloco.querySelector("[data-baixar-rotulo]");
      var versao = bloco.querySelector("[data-baixar-versao]");
      var reserva = bloco.querySelector("[data-baixar-reserva]");
      if (!link) {
        return;
      }
      bloco.setAttribute("data-estado", estado.tipo);
      /* A linha de reserva só aparece quando não dá para confirmar a versão. */
      if (reserva) {
        reserva.hidden = estado.tipo === "pronto" || estado.tipo === "sem-versao";
      }

      if (estado.tipo === "pronto") {
        link.href = estado.url;
        if (rotulo) {
          rotulo.textContent = ROTULO_BAIXAR;
        }
        if (versao) {
          var partes = [];
          if (estado.versao) {
            partes.push("Versão " + estado.versao);
          }
          if (estado.tamanho) {
            partes.push(estado.tamanho);
          }
          versao.textContent = partes.length ? partes.join(", ") + "." : "";
          versao.hidden = partes.length === 0;
        }
        return;
      }

      if (estado.tipo === "sem-versao") {
        link.href = PAGINA_VERSOES;
        if (rotulo) {
          rotulo.textContent = "Ver a página de versões";
        }
        if (versao) {
          versao.textContent = "A primeira versão está chegando. Quando ela sair, este botão baixa o instalador.";
          versao.hidden = false;
        }
        return;
      }

      /* verificando ou erro (API lenta, fora do ar ou no limite de consultas): o botão
         continua baixando o instalador direto, sem número de versão, e a linha de reserva
         explica qual arquivo escolher na página de versões. */
      link.href = DOWNLOAD_ULTIMA;
      if (rotulo) {
        rotulo.textContent = ROTULO_BAIXAR;
      }
      if (versao) {
        versao.hidden = true;
        versao.textContent = "";
      }
    });
  }

  function consultarVersoes() {
    if (typeof fetch !== "function") {
      return Promise.resolve({ tipo: "erro" });
    }
    var controle = typeof AbortController === "function" ? new AbortController() : null;
    var relogio = setTimeout(function () {
      if (controle) {
        controle.abort();
      }
    }, ESPERA_MAXIMA_MS);

    return fetch(API_VERSOES, {
      headers: { Accept: "application/vnd.github+json" },
      credentials: "omit",
      referrerPolicy: "no-referrer",
      signal: controle ? controle.signal : undefined
    })
      .then(function (resposta) {
        if (resposta.status === 404) {
          return { tipo: "sem-versao" };
        }
        if (!resposta.ok) {
          return { tipo: "erro" };
        }
        return resposta.json().then(function (lista) {
          return escolherVersao(lista) || { tipo: "sem-versao" };
        });
      })
      .catch(function () {
        return { tipo: "erro" };
      })
      .then(function (estado) {
        clearTimeout(relogio);
        return estado;
      });
  }

  function prepararDownload() {
    if (!document.querySelector("[data-baixar]")) {
      return;
    }
    aplicarEstado({ tipo: "verificando" });
    consultarVersoes().then(aplicarEstado);

    document.addEventListener("click", function (evento) {
      var alvo = evento.target;
      var link = alvo && alvo.closest ? alvo.closest("[data-baixar-link]") : null;
      if (!link) {
        return;
      }
      var bloco = link.closest("[data-baixar]");
      if (!bloco || bloco.getAttribute("data-estado") !== "pronto") {
        return;
      }
      var depois = bloco.querySelector("[data-baixar-depois]");
      if (depois) {
        depois.hidden = false;
      }
    });
  }

  /* ---------- Dia de exemplo ---------- */

  function prepararExemplo() {
    var grupo = document.querySelector("[data-escolhas]");
    var folha = document.querySelector("[data-folha]");
    if (!grupo || !folha) {
      return;
    }
    var botoes = todos("[data-area]", grupo);
    var blocos = todos("[data-lista]", folha);
    var rotulo = folha.querySelector("[data-folha-area]");
    grupo.hidden = false;

    botoes.forEach(function (botao) {
      botao.addEventListener("click", function () {
        var area = botao.getAttribute("data-area");
        botoes.forEach(function (outro) {
          outro.setAttribute("aria-pressed", outro === botao ? "true" : "false");
        });
        blocos.forEach(function (bloco) {
          bloco.hidden = bloco.getAttribute("data-lista") !== area;
        });
        if (rotulo) {
          rotulo.textContent = botao.textContent;
        }
        folha.classList.remove("animar");
        void folha.offsetWidth;
        folha.classList.add("animar");
      });
    });
  }

  /* ---------- Mandar o link para o computador ---------- */

  function prepararCompartilhar() {
    var botoes = todos("[data-compartilhar]");
    if (!botoes.length) {
      return;
    }
    var podeCompartilhar = typeof navigator.share === "function";
    var podeCopiar = Boolean(window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText);
    if (!podeCompartilhar && !podeCopiar) {
      return;
    }
    var endereco = location.href.split("#")[0];

    botoes.forEach(function (botao) {
      var aviso = botao.parentNode.querySelector("[data-compartilhar-aviso]");
      botao.hidden = false;
      botao.addEventListener("click", function () {
        if (podeCompartilhar) {
          navigator
            .share({ title: "Manda Currículo", text: "Abrir no computador para baixar o Manda Currículo", url: endereco })
            .catch(function () {});
          return;
        }
        navigator.clipboard.writeText(endereco).then(
          function () {
            if (aviso) {
              aviso.textContent = "Link copiado. Cole numa conversa com você mesmo ou no seu e-mail e abra no computador.";
            }
          },
          function () {
            if (aviso) {
              aviso.textContent = "Não deu para copiar. O endereço é " + endereco;
            }
          }
        );
      });
    });
  }

  function iniciar() {
    prepararExemplo();
    prepararCompartilhar();
    prepararDownload();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", iniciar);
  } else {
    iniciar();
  }
})();
