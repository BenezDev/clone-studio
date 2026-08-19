#!/usr/bin/env bash
# Funções compartilhadas pelos scripts do Local Clone Studio.
# shellcheck shell=bash

set -uo pipefail

# --- localização do projeto -------------------------------------------------
# Resolve a raiz mesmo com espaços e acentos no caminho.
CS_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CS_ROOT="$(cd -- "${CS_SCRIPT_DIR}/.." && pwd)"
export CLONE_STUDIO_ROOT="${CS_ROOT}"

# `python -m apps.cli.main` só resolve o pacote se a raiz estiver no sys.path,
# e o `-m` do Python coloca lá apenas o diretório atual. Sem isto, todos os
# wrappers falham com ModuleNotFoundError quando invocados de fora da raiz —
# que é exatamente como um usuário chama `~/Clone\ Studio/scripts/doctor.sh`.
export PYTHONPATH="${CS_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

CS_ENVS="${CS_ROOT}/.envs"
CS_RUNTIME="${CS_ROOT}/.runtime"
CS_LOGS="${CS_ROOT}/logs"

# --- cores ------------------------------------------------------------------
if [[ -t 1 ]] && [[ "${NO_COLOR:-}" != "1" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'; C_CYAN=$'\033[36m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""
  C_BLUE=""; C_CYAN=""
fi

say()    { printf '%s\n' "$*"; }
info()   { printf '%s»%s %s\n' "${C_BLUE}" "${C_RESET}" "$*"; }
ok()     { printf '%s✓%s %s\n' "${C_GREEN}" "${C_RESET}" "$*"; }
warn()   { printf '%s!%s %s\n' "${C_YELLOW}" "${C_RESET}" "$*" >&2; }
fail()   { printf '%s✗%s %s\n' "${C_RED}" "${C_RESET}" "$*" >&2; }
step()   { printf '\n%s%s%s\n' "${C_BOLD}${C_CYAN}" "$*" "${C_RESET}"; }
detail() { printf '  %s%s%s\n' "${C_DIM}" "$*" "${C_RESET}"; }

die() { fail "$*"; exit 1; }

# Mostra o comando privilegiado em vez de executá-lo silenciosamente.
needs_sudo() {
  warn "Esta ação precisa de privilégios de administrador."
  say  "  Rode você mesmo:"
  say  "      ${C_BOLD}$*${C_RESET}"
}

have() { command -v "$1" >/dev/null 2>&1; }

# confirm "pergunta" [padrão_sim]
confirm() {
  local prompt="$1" default="${2:-n}" reply
  if [[ "${CS_ASSUME_YES:-0}" == "1" ]]; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    # Sem terminal interativo: nunca assume "sim" para downloads/instalações.
    warn "Sem terminal interativo; assumindo 'não' para: ${prompt}"
    return 1
  fi
  local hint="[s/N]"
  [[ "${default}" == "s" ]] && hint="[S/n]"
  read -r -p "  ${prompt} ${hint} " reply || return 1
  reply="${reply:-${default}}"
  [[ "${reply}" =~ ^[SsYy]$ ]]
}

env_python() { printf '%s/%s/bin/python' "${CS_ENVS}" "$1"; }

env_exists() { [[ -x "$(env_python "$1")" ]]; }

# Interpretador do backend, com fallback para o Python do sistema.
backend_python() {
  if env_exists backend; then
    env_python backend
  else
    command -v python3
  fi
}

human_size() {
  local bytes="$1"
  if (( bytes >= 1073741824 )); then
    printf '%.1f GB' "$(echo "${bytes} 1073741824" | awk '{print $1/$2}')"
  elif (( bytes >= 1048576 )); then
    printf '%.0f MB' "$(echo "${bytes} 1048576" | awk '{print $1/$2}')"
  else
    printf '%d B' "${bytes}"
  fi
}

port_in_use() {
  local port="$1"
  if have ss; then
    ss -ltn "sport = :${port}" 2>/dev/null | grep -q LISTEN
  elif have lsof; then
    lsof -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

wait_for_port() {
  local port="$1" timeout="${2:-60}" elapsed=0
  while (( elapsed < timeout )); do
    port_in_use "${port}" && return 0
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 1
}
