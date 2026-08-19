#!/usr/bin/env bash
#
# Local Clone Studio — inicialização.
#
# Sobe backend e interface, monitora os processos e encerra os filhos
# corretamente no CTRL+C (inclusive netos, via process group).
#
# Uso:
#   ./start.sh              backend + interface
#   ./start.sh --api-only   apenas o backend
#   ./start.sh --no-open    não abre o navegador

set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/scripts/lib.sh"

API_ONLY=0
OPEN_BROWSER=1
DEV_MODE="${CLONE_STUDIO_DEV:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-only) API_ONLY=1; shift ;;
    --no-open)  OPEN_BROWSER=0; shift ;;
    --dev)      DEV_MODE=1; shift ;;
    -h|--help)
      say "uso: ./start.sh [--api-only] [--no-open] [--dev]"; exit 0 ;;
    *) die "argumento desconhecido: $1" ;;
  esac
done

mkdir -p "${CS_RUNTIME}" "${CS_LOGS}"

# ---------------------------------------------------------------------------
# Pré-requisitos
# ---------------------------------------------------------------------------
env_exists backend || die "Ambiente do backend ausente. Rode ./install.sh primeiro."
have ffmpeg || die "FFmpeg não encontrado. Instale com: sudo apt install ffmpeg"

read -r API_HOST API_PORT WEB_PORT < <(
  "$(backend_python)" - <<'PYTHON'
from core.config.loader import load_settings
s = load_settings()
print(s.app.host, s.app.api_port, s.app.web_port)
PYTHON
) || die "Não foi possível ler a configuração."

if port_in_use "${API_PORT}"; then
  die "A porta ${API_PORT} já está em uso. Rode ./stop.sh ou mude app.api_port em config/local.yaml."
fi

# ---------------------------------------------------------------------------
# Encerramento limpo
# ---------------------------------------------------------------------------
CHILD_PIDS=()

shutdown() {
  local code=$?
  trap '' INT TERM
  say ""
  info "encerrando…"

  for pid in "${CHILD_PIDS[@]:-}"; do
    [[ -z "${pid}" ]] && continue
    if kill -0 "${pid}" 2>/dev/null; then
      # Mata o grupo inteiro: o Vite e o uvicorn --reload criam netos.
      kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null
    fi
  done

  local waited=0
  while (( waited < 10 )); do
    local alive=0
    for pid in "${CHILD_PIDS[@]:-}"; do
      [[ -z "${pid}" ]] && continue
      kill -0 "${pid}" 2>/dev/null && alive=1
    done
    (( alive )) || break
    sleep 0.5
    waited=$((waited + 1))
  done

  for pid in "${CHILD_PIDS[@]:-}"; do
    [[ -z "${pid}" ]] && continue
    if kill -0 "${pid}" 2>/dev/null; then
      warn "forçando encerramento do PID ${pid}"
      kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "${pid}" 2>/dev/null
    fi
  done

  rm -f "${CS_RUNTIME}/api.pid" "${CS_RUNTIME}/web.pid"
  ok "encerrado."
  exit "${code}"
}
trap shutdown INT TERM EXIT

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------
step "Iniciando backend"
detail "http://${API_HOST}:${API_PORT}"

UVICORN_ARGS=(--host "${API_HOST}" --port "${API_PORT}" --log-level info)
if [[ "${DEV_MODE}" == "1" ]]; then
  # Recarrega só o código da aplicação; .envs/ e models/ ficariam pesados
  # demais para o watcher.
  UVICORN_ARGS+=(--reload
    --reload-dir "${CS_ROOT}/apps/api"
    --reload-dir "${CS_ROOT}/core"
    --reload-dir "${CS_ROOT}/services")
  detail "modo desenvolvimento: hot reload ativo"
fi

setsid "$(backend_python)" -m uvicorn apps.api.app.main:app \
  "${UVICORN_ARGS[@]}" \
  >> "${CS_LOGS}/api.out" 2>&1 &
API_PID=$!
CHILD_PIDS+=("${API_PID}")
echo "${API_PID}" > "${CS_RUNTIME}/api.pid"

if wait_for_port "${API_PORT}" 45; then
  ok "backend no ar (PID ${API_PID})"
else
  fail "o backend não subiu em 45s. Últimas linhas de logs/api.out:"
  tail -25 "${CS_LOGS}/api.out" | sed 's/^/    /'
  exit 1
fi

# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------
WEB_URL="http://${API_HOST}:${API_PORT}"

if (( ! API_ONLY )) && [[ -f "${CS_ROOT}/apps/web/package.json" ]]; then
  step "Iniciando interface"

  if [[ ! -d "${CS_ROOT}/apps/web/node_modules" ]]; then
    warn "dependências do frontend ausentes — rodando a instalação agora"
    if have pnpm; then
      ( cd "${CS_ROOT}/apps/web" && pnpm install ) || warn "pnpm install falhou"
    elif have npm; then
      ( cd "${CS_ROOT}/apps/web" && npm install ) || warn "npm install falhou"
    fi
  fi

  RUNNER=""
  have pnpm && RUNNER="pnpm"
  [[ -z "${RUNNER}" ]] && have npm && RUNNER="npm"

  if [[ -n "${RUNNER}" ]]; then
    ( cd "${CS_ROOT}/apps/web" && setsid "${RUNNER}" run dev \
        >> "${CS_LOGS}/web.out" 2>&1 ) &
    WEB_PID=$!
    CHILD_PIDS+=("${WEB_PID}")
    echo "${WEB_PID}" > "${CS_RUNTIME}/web.pid"

    if wait_for_port "${WEB_PORT}" 60; then
      WEB_URL="http://${API_HOST}:${WEB_PORT}"
      ok "interface no ar (PID ${WEB_PID})"
    else
      warn "a interface não subiu; veja logs/web.out"
    fi
  else
    warn "pnpm/npm não encontrados — apenas a API está disponível"
  fi
fi

# ---------------------------------------------------------------------------
# Pronto
# ---------------------------------------------------------------------------
say ""
say "  ${C_BOLD}Local Clone Studio${C_RESET}"
say "  ────────────────────────────────────────────"
say "  Interface : ${C_BOLD}${WEB_URL}${C_RESET}"
say "  API       : http://${API_HOST}:${API_PORT}/api/docs"
say "  Logs      : logs/api.out · logs/web.out"
say ""
say "  ${C_DIM}CTRL+C encerra tudo.${C_RESET}"
say ""

if (( OPEN_BROWSER )) && have xdg-open; then
  ( sleep 1; xdg-open "${WEB_URL}" >/dev/null 2>&1 ) &
fi

# ---------------------------------------------------------------------------
# Monitoramento
# ---------------------------------------------------------------------------
while true; do
  sleep 2
  for pid in "${CHILD_PIDS[@]}"; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      fail "o processo ${pid} morreu inesperadamente."
      if [[ "${pid}" == "${API_PID}" ]]; then
        say "  Últimas linhas de logs/api.out:"
        tail -25 "${CS_LOGS}/api.out" | sed 's/^/    /'
      else
        say "  Últimas linhas de logs/web.out:"
        tail -25 "${CS_LOGS}/web.out" | sed 's/^/    /'
      fi
      exit 1
    fi
  done
done
