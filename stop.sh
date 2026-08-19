#!/usr/bin/env bash
#
# Encerra o Local Clone Studio.
set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/scripts/lib.sh"

stopped=0

stop_pidfile() {
  local name="$1" file="${CS_RUNTIME}/$1.pid"
  [[ -f "${file}" ]] || return 0
  local pid
  pid="$(cat "${file}" 2>/dev/null)"
  rm -f "${file}"
  [[ -z "${pid}" ]] && return 0

  if ! kill -0 "${pid}" 2>/dev/null; then
    detail "${name}: PID ${pid} já não existe"
    return 0
  fi

  info "encerrando ${name} (PID ${pid})"
  kill -TERM -- "-${pid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null
  local waited=0
  while (( waited < 20 )) && kill -0 "${pid}" 2>/dev/null; do
    sleep 0.5; waited=$((waited + 1))
  done
  if kill -0 "${pid}" 2>/dev/null; then
    warn "${name} não respondeu ao TERM; forçando"
    kill -KILL -- "-${pid}" 2>/dev/null || kill -KILL "${pid}" 2>/dev/null
  fi
  stopped=$((stopped + 1))
}

step "Encerrando Local Clone Studio"
stop_pidfile api
stop_pidfile web

# Rede de segurança: processos deixados por um encerramento anterior sujo.
#
# O padrão do Vite precisa aceitar as duas ordens. A linha de comando real é
#   node <raiz>/apps/web/node_modules/.bin/../vite/bin/vite.js
# ou seja, 'apps/web' aparece ANTES de 'vite' — um padrão 'vite.*apps/web'
# nunca casa e deixa a porta 3000 ocupada.
#
# `pgrep -f` também casaria com o próprio stop.sh (a linha de comando dele
# contém os padrões), então processos com PID igual ao nosso são ignorados.
for pattern in "uvicorn apps.api.app.main:app" "apps/web.*vite|vite.*apps/web"; do
  while read -r pid; do
    [[ -z "${pid}" ]] && continue
    [[ "${pid}" == "$$" || "${pid}" == "${PPID}" ]] && continue
    warn "processo remanescente encontrado (PID ${pid})"
    kill -TERM "${pid}" 2>/dev/null && stopped=$((stopped + 1))
  done < <(pgrep -f "${pattern}" 2>/dev/null || true)
done

if (( stopped )); then
  ok "${stopped} processo(s) encerrado(s)."
else
  ok "nada estava rodando."
fi
