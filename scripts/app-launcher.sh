#!/usr/bin/env bash
#
# Ponto de entrada gráfico do Local Clone Studio.
#
# Diferente do ./start.sh, este script é feito para ser clicado: não deixa
# terminal aberto, sobrevive ao fechamento da sessão que o lançou e conversa
# com o usuário por notificação e diálogo em vez de stdout.
#
# Comportamento de alternância: se a aplicação já estiver no ar, apenas abre o
# navegador. Clicar duas vezes no ícone nunca sobe uma segunda instância.
#
# Uso:
#   app-launcher.sh            inicia (ou abre, se já estiver rodando)
#   app-launcher.sh --stop     encerra
#   app-launcher.sh --doctor   diagnóstico numa janela de terminal

set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

APP_NAME="Local Clone Studio"
ICON="${CS_ROOT}/assets/clone-studio.svg"
LAUNCH_LOG="${CS_LOGS}/launcher.log"
BOOT_TIMEOUT=120

mkdir -p "${CS_LOGS}" "${CS_RUNTIME}"

# --- diálogo -----------------------------------------------------------------

notify() { # título corpo [urgência]
  have notify-send || return 0
  notify-send --app-name="${APP_NAME}" --icon="${ICON}" \
    --urgency="${3:-normal}" "$1" "$2" 2>/dev/null || true
}

error_dialog() { # título corpo
  if have zenity; then
    zenity --error --width=520 --title="${APP_NAME}" \
      --text="$1\n\n$2" 2>/dev/null || true
  else
    notify "$1" "$2" critical
  fi
}

# Pergunta se o usuário quer ver o log; devolve 0 se sim.
offer_log() {
  have zenity || return 1
  zenity --question --width=520 --title="${APP_NAME}" \
    --text="$1\n\nQuer abrir o log para ver o que aconteceu?" \
    --ok-label="Abrir log" --cancel-label="Fechar" 2>/dev/null
}

read_ports() {
  "$(backend_python)" - <<'PYTHON' 2>/dev/null
from core.config.loader import load_settings
s = load_settings()
print(s.app.host, s.app.api_port, s.app.web_port)
PYTHON
}

# --- ações -------------------------------------------------------------------

do_stop() {
  "${CS_ROOT}/stop.sh" >> "${LAUNCH_LOG}" 2>&1
  notify "${APP_NAME}" "Encerrado."
}

do_doctor() {
  local term
  term="$(command -v x-terminal-emulator || command -v gnome-terminal || true)"
  if [[ -z "${term}" ]]; then
    error_dialog "Nenhum terminal encontrado." \
      "Rode manualmente: ${CS_ROOT}/scripts/doctor.sh"
    return 1
  fi
  # `-e` com um comando composto exige bash -c; o `read` segura a janela
  # aberta para o usuário conseguir ler o resultado.
  "${term}" -e bash -c \
    "'${CS_ROOT}/scripts/doctor.sh'; echo; read -rp 'Enter para fechar…'" &
}

do_start() {
  if [[ ! -x "$(backend_python)" ]]; then
    error_dialog "O Local Clone Studio ainda não foi instalado." \
      "Abra um terminal na pasta do projeto e rode:\n\n    ./install.sh"
    return 1
  fi

  local host api_port web_port
  if ! read -r host api_port web_port < <(read_ports); then
    error_dialog "Não foi possível ler a configuração." \
      "Verifique config/default.yaml e config/local.yaml."
    return 1
  fi

  # Já no ar? Só abre o navegador — clicar de novo não sobe outra instância.
  local url="http://${host}:${web_port}"
  if curl -sf --max-time 2 "http://${host}:${api_port}/api/health" >/dev/null 2>&1; then
    port_in_use "${web_port}" || url="http://${host}:${api_port}"
    xdg-open "${url}" >/dev/null 2>&1 &
    notify "${APP_NAME}" "Já estava aberto."
    return 0
  fi

  if port_in_use "${api_port}"; then
    error_dialog "A porta ${api_port} está ocupada por outro programa." \
      "Encerre-o ou altere app.api_port em config/local.yaml."
    return 1
  fi

  notify "${APP_NAME}" "Iniciando… os modelos levam alguns segundos para subir."

  # `setsid` desacopla do processo que veio do menu: fechar a sessão gráfica
  # que lançou o ícone não derruba a aplicação.
  : > "${LAUNCH_LOG}"
  setsid "${CS_ROOT}/start.sh" --no-open >> "${LAUNCH_LOG}" 2>&1 < /dev/null &

  # Espera a interface. Se ela não subir mas a API sim, ainda é utilizável.
  local waited=0
  while (( waited < BOOT_TIMEOUT )); do
    if port_in_use "${web_port}"; then
      xdg-open "http://${host}:${web_port}" >/dev/null 2>&1 &
      notify "${APP_NAME}" "Pronto — http://${host}:${web_port}"
      return 0
    fi
    if ! port_in_use "${api_port}" && (( waited > 20 )); then
      break  # nem a API subiu: falhou cedo
    fi
    sleep 2
    waited=$((waited + 2))
  done

  if port_in_use "${api_port}"; then
    xdg-open "http://${host}:${api_port}/api/docs" >/dev/null 2>&1 &
    notify "${APP_NAME}" \
      "A API subiu, mas a interface não. Veja logs/web.out." critical
    return 0
  fi

  local tail_log
  tail_log="$(tail -n 12 "${LAUNCH_LOG}" 2>/dev/null)"
  if offer_log "O Local Clone Studio não conseguiu iniciar."; then
    xdg-open "${LAUNCH_LOG}" >/dev/null 2>&1 &
  else
    error_dialog "O Local Clone Studio não conseguiu iniciar." \
      "Últimas linhas do log:\n\n${tail_log}"
  fi
  return 1
}

# --- entrada -----------------------------------------------------------------

case "${1:-}" in
  --stop)   do_stop ;;
  --doctor) do_doctor ;;
  ""|--start) do_start ;;
  *) echo "uso: app-launcher.sh [--start|--stop|--doctor]" >&2; exit 2 ;;
esac
