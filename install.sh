#!/usr/bin/env bash
#
# Local Clone Studio — instalador.
#
# Princípios:
#   * nunca roda `sudo` sozinho — mostra o comando e deixa você decidir;
#   * nunca baixa pesos grandes sem perguntar;
#   * não altera nada fora do diretório do projeto (exceto `uv`, e só com
#     autorização explícita, dentro de ~/.local/bin);
#   * cada engine de ML ganha seu próprio ambiente virtual.
#
# Uso:
#   ./install.sh                    instalação padrão (backend + TTS + whisper + web)
#   ./install.sh --with-musetalk    inclui o ambiente de lip-sync (Python 3.10 via uv)
#   ./install.sh --only qwen-tts    reinstala apenas um ambiente
#   ./install.sh --yes              não pergunta nada (usa os padrões seguros)
#   ./install.sh --skip-frontend    pula pnpm install
#   ./install.sh --list             mostra os alvos disponíveis

set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/scripts/lib.sh"

ONLY=""
WITH_MUSETALK=0
SKIP_FRONTEND=0
OFFER_MODELS=1
CS_ASSUME_YES=0
FAILURES=()

usage() {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --only)          ONLY="${2:-}"; shift 2 ;;
    --with-musetalk) WITH_MUSETALK=1; shift ;;
    --skip-frontend) SKIP_FRONTEND=1; shift ;;
    --no-models)     OFFER_MODELS=0; shift ;;
    --yes|-y)        CS_ASSUME_YES=1; shift ;;
    --list)          say "alvos: backend qwen-tts whisper musetalk frontend"; exit 0 ;;
    -h|--help)       usage ;;
    *) die "Argumento desconhecido: $1 (use --help)" ;;
  esac
done
export CS_ASSUME_YES

wants() { [[ -z "${ONLY}" || "${ONLY}" == "$1" ]]; }

record_failure() { FAILURES+=("$1"); fail "$1"; }

# ---------------------------------------------------------------------------
# 0. Apresentação
# ---------------------------------------------------------------------------
say ""
say "${C_BOLD}  Local Clone Studio — instalador${C_RESET}"
say "${C_DIM}  ${CS_ROOT}${C_RESET}"

# ---------------------------------------------------------------------------
# 1. Sistema e hardware
# ---------------------------------------------------------------------------
step "1. Detectando sistema e hardware"

if ! have python3; then
  fail "Python 3 não encontrado."
  needs_sudo "sudo apt install -y python3 python3-venv python3-pip"
  exit 1
fi

PY_VERSION="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3,10) else 0)')"
if [[ "${PY_OK}" != "1" ]]; then
  die "Python ${PY_VERSION} é antigo demais. O projeto exige 3.10 ou superior."
fi
ok "Python ${PY_VERSION} ($(command -v python3))"

if ! python3 -c 'import venv' >/dev/null 2>&1; then
  fail "O módulo 'venv' não está disponível."
  needs_sudo "sudo apt install -y python3-venv"
  exit 1
fi

python3 -m core.hardware.detect 2>/dev/null | sed 's/^/  /' || \
  warn "Não foi possível executar a detecção detalhada de hardware."

HAS_NVIDIA=0
have nvidia-smi && nvidia-smi -L >/dev/null 2>&1 && HAS_NVIDIA=1
if (( HAS_NVIDIA )); then
  TORCH_INDEX="https://download.pytorch.org/whl/cu124"
  detail "GPU NVIDIA detectada → torch será instalado com CUDA."
else
  TORCH_INDEX="https://download.pytorch.org/whl/cpu"
  detail "Sem GPU NVIDIA → torch CPU (evita ~2 GB de bibliotecas CUDA inúteis)."
fi

# ---------------------------------------------------------------------------
# 2. Ferramentas de sistema
# ---------------------------------------------------------------------------
step "2. Verificando ferramentas de sistema"

MISSING_APT=()
check_tool() { # nome pacote_apt obrigatório
  if have "$1"; then
    ok "$1 — $(command -v "$1")"
  elif [[ "$3" == "obrigatorio" ]]; then
    fail "$1 ausente (obrigatório)"
    MISSING_APT+=("$2")
  else
    warn "$1 ausente (opcional)"
  fi
}

check_tool ffmpeg  ffmpeg          obrigatorio
check_tool ffprobe ffmpeg          obrigatorio
check_tool git     git             obrigatorio
check_tool curl    curl            obrigatorio

if (( ${#MISSING_APT[@]} > 0 )); then
  say ""
  fail "Faltam dependências de sistema."
  # shellcheck disable=SC2207
  UNIQUE=($(printf '%s\n' "${MISSING_APT[@]}" | sort -u))
  needs_sudo "sudo apt update && sudo apt install -y ${UNIQUE[*]}"
  exit 1
fi

if have node; then
  NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
  if (( NODE_MAJOR >= 20 )); then
    ok "node $(node -v)"
  else
    warn "node $(node -v) — a interface pede Node 20+."
  fi
else
  warn "node ausente — a interface web não será instalada."
  SKIP_FRONTEND=1
fi

if have pnpm; then
  ok "pnpm $(pnpm --version)"
elif have npm && (( ! SKIP_FRONTEND )); then
  warn "pnpm ausente. Instale com: npm install -g pnpm  (ou: corepack enable)"
fi

mkdir -p "${CS_ENVS}" "${CS_LOGS}" "${CS_RUNTIME}"

# ---------------------------------------------------------------------------
# Helper de criação de ambiente
# ---------------------------------------------------------------------------
create_venv() { # nome
  local name="$1" dir="${CS_ENVS}/$1"
  if [[ -x "${dir}/bin/python" ]]; then
    detail "ambiente '${name}' já existe — reaproveitando"
    return 0
  fi
  info "criando ambiente '${name}' (Python ${PY_VERSION})"
  python3 -m venv "${dir}" || return 1
  "${dir}/bin/python" -m pip install --quiet --upgrade pip setuptools wheel || return 1
}

pip_install() { # nome args...
  local name="$1"; shift
  "${CS_ENVS}/${name}/bin/python" -m pip install --disable-pip-version-check "$@"
}

# ---------------------------------------------------------------------------
# 3. Backend
# ---------------------------------------------------------------------------
if wants backend; then
  step "3. Ambiente do backend"
  if create_venv backend && \
     pip_install backend -r "${CS_ROOT}/requirements/backend.txt"; then
    ok "backend pronto"
  else
    record_failure "falha ao preparar o ambiente do backend"
  fi
fi

# ---------------------------------------------------------------------------
# 4. Qwen3-TTS
# ---------------------------------------------------------------------------
if wants qwen-tts; then
  step "4. Ambiente do Qwen3-TTS (clonagem de voz)"
  if create_venv qwen-tts; then
    info "instalando torch a partir de ${TORCH_INDEX}"
    if pip_install qwen-tts --index-url "${TORCH_INDEX}" \
         torch==2.9.1 torchaudio==2.9.1 && \
       pip_install qwen-tts -r "${CS_ROOT}/requirements/qwen-tts.txt"; then
      ok "qwen-tts pronto"
    else
      record_failure "falha ao instalar o ambiente qwen-tts"
    fi
  else
    record_failure "falha ao criar o ambiente qwen-tts"
  fi
fi

# ---------------------------------------------------------------------------
# 5. Transcrição
# ---------------------------------------------------------------------------
if wants whisper; then
  step "5. Ambiente de transcrição (legendas)"
  if create_venv whisper && \
     pip_install whisper -r "${CS_ROOT}/requirements/whisper.txt"; then
    ok "whisper pronto"
  else
    record_failure "falha ao instalar o ambiente whisper"
  fi
fi

# ---------------------------------------------------------------------------
# 6. MuseTalk (opcional — exige Python 3.10)
# ---------------------------------------------------------------------------
install_musetalk() {
  step "6. Ambiente do MuseTalk (lip-sync)"

  say "  O MuseTalk exige Python 3.10 + torch 2.0.1 + mmcv 2.0.1."
  say "  Este sistema tem Python ${PY_VERSION}, então é preciso um CPython 3.10"
  say "  isolado. A ferramenta ${C_BOLD}uv${C_RESET} faz isso sem tocar no Python do sistema."
  say ""

  if ! have uv; then
    if ! confirm "Instalar 'uv' em ~/.local/bin? (não requer sudo)" s; then
      warn "MuseTalk pulado. Rode ./install.sh --with-musetalk quando quiser."
      return 1
    fi
    info "baixando uv de https://astral.sh/uv/install.sh"
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
      record_failure "falha ao instalar uv"
      return 1
    fi
    export PATH="${HOME}/.local/bin:${PATH}"
    have uv || { record_failure "uv instalado mas fora do PATH"; return 1; }
  fi
  ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

  info "provisionando CPython 3.10"
  uv python install 3.10 || { record_failure "uv não conseguiu instalar Python 3.10"; return 1; }

  local dir="${CS_ENVS}/musetalk"
  if [[ ! -x "${dir}/bin/python" ]]; then
    uv venv --python 3.10 "${dir}" || { record_failure "falha ao criar venv 3.10"; return 1; }
  fi

  local musetalk_index="https://download.pytorch.org/whl/cpu"
  (( HAS_NVIDIA )) && musetalk_index="https://download.pytorch.org/whl/cu118"

  info "instalando torch 2.0.1 (${musetalk_index})"
  uv pip install --python "${dir}/bin/python" --index-url "${musetalk_index}" \
      torch==2.0.1 torchvision==0.15.2 torchaudio==2.0.2 \
    || { record_failure "falha ao instalar torch 2.0.1"; return 1; }

  info "instalando dependências do MuseTalk"
  uv pip install --python "${dir}/bin/python" \
      -r "${CS_ROOT}/requirements/musetalk.txt" \
    || { record_failure "falha nas dependências do MuseTalk"; return 1; }

  # O stack MMLab é de 2023 e assume um ambiente daquela época. Três detalhes
  # quebram a instalação numa máquina moderna, todos tratados aqui:
  #
  #  1. setuptools >= 81 removeu `pkg_resources`, que o mmengine ainda importa;
  #  2. `chumpy` (dependência do mmpose) tem setup.py legado que importa `pip`
  #     em tempo de build — o build isolado do uv não tem pip;
  #  3. `mmdet` é obrigatório mesmo o MuseTalk nunca o importando: a config do
  #     DWPose declara o backbone como `_scope_='mmdet', type='CSPNeXt'`, e o
  #     registro do mmpose só resolve esse nome com o mmdet instalado.
  info "preparando ferramentas de build (setuptools < 81 por causa do pkg_resources)"
  uv pip install --python "${dir}/bin/python" \
      pip "setuptools==75.8.0" wheel \
    || { record_failure "falha ao preparar ferramentas de build"; return 1; }

  info "instalando stack MMLab (mmengine/mmcv/mmpose)"
  uv pip install --python "${dir}/bin/python" "mmengine==0.10.4" \
    || { record_failure "falha ao instalar mmengine"; return 1; }
  uv pip install --python "${dir}/bin/python" "mmcv==2.0.1" \
      --find-links https://download.openmmlab.com/mmcv/dist/cpu/torch2.0.0/index.html \
    || { record_failure "falha ao instalar mmcv 2.0.1"; return 1; }
  uv pip install --python "${dir}/bin/python" --no-build-isolation "chumpy==0.70" \
    || { record_failure "falha ao compilar chumpy"; return 1; }
  uv pip install --python "${dir}/bin/python" "mmpose==1.1.0" "mmdet==3.1.0" \
    || { record_failure "falha ao instalar mmpose/mmdet"; return 1; }

  # mmpose/mmdet reinstalam setuptools moderno como dependência transitiva.
  uv pip install --python "${dir}/bin/python" "setuptools==75.8.0" >/dev/null 2>&1

  info "validando imports do stack"
  "${dir}/bin/python" -c "
from mmpose.apis import inference_topdown, init_model
from mmpose.structures import merge_data_samples
import torch, mmcv, mmdet, pkg_resources
print(f'  torch {torch.__version__} · mmcv {mmcv.__version__} · mmdet {mmdet.__version__}')
" || { record_failure "stack MMLab instalado mas não importa"; return 1; }

  ok "musetalk pronto"
}

if [[ "${ONLY}" == "musetalk" ]] || { [[ -z "${ONLY}" ]] && (( WITH_MUSETALK )); }; then
  install_musetalk || true
elif [[ -z "${ONLY}" ]]; then
  step "6. Ambiente do MuseTalk (lip-sync)"
  detail "pulado — rode ./install.sh --with-musetalk para instalar."
  detail "Ele exige Python 3.10 isolado e ~2 GB de dependências."
fi

# ---------------------------------------------------------------------------
# 7. Interface web
# ---------------------------------------------------------------------------
if wants frontend && (( ! SKIP_FRONTEND )); then
  step "7. Interface web"
  if [[ -f "${CS_ROOT}/apps/web/package.json" ]]; then
    if have pnpm; then
      ( cd "${CS_ROOT}/apps/web" && pnpm install --silent ) \
        && ok "dependências do frontend instaladas" \
        || record_failure "pnpm install falhou"
    elif have npm; then
      ( cd "${CS_ROOT}/apps/web" && npm install --silent ) \
        && ok "dependências do frontend instaladas (npm)" \
        || record_failure "npm install falhou"
    else
      warn "nem pnpm nem npm disponíveis — frontend não instalado"
    fi
  else
    detail "apps/web ainda não tem package.json — pulando"
  fi
fi

# ---------------------------------------------------------------------------
# 8. Estado inicial
# ---------------------------------------------------------------------------
step "8. Preparando diretórios e perfil de hardware"
"$(backend_python)" - <<'PYTHON' || warn "não foi possível gravar o perfil de hardware"
from core.storage.paths import get_paths
from core.hardware.detect import detect, save_report

paths = get_paths()
paths.ensure_runtime_dirs()
report = detect(disk_target=paths.root)
save_report(report, paths.hardware_profile_file)
print(f"  perfil de hardware: {report.profile} -> {paths.hardware_profile_file.name}")
PYTHON
ok "diretórios de dados prontos"

# ---------------------------------------------------------------------------
# 9. Modelos
# ---------------------------------------------------------------------------
if (( OFFER_MODELS )) && [[ -z "${ONLY}" ]]; then
  step "9. Pesos dos modelos"
  say "  Nenhum peso foi baixado ainda. O pipeline mínimo precisa de:"
  "$(backend_python)" -m scripts.models list 2>/dev/null | sed 's/^/  /' || \
    detail "(rode ./scripts/models.sh list para ver o catálogo)"
  say ""
  if confirm "Baixar agora os modelos essenciais do MVP?" n; then
    "${CS_ROOT}/scripts/models.sh" install --essential || \
      record_failure "download de modelos falhou"
  else
    say "  Depois, quando quiser:"
    say "      ${C_BOLD}./scripts/models.sh install --essential${C_RESET}"
  fi
fi

# ---------------------------------------------------------------------------
# 10. Diagnóstico
# ---------------------------------------------------------------------------
step "10. Diagnóstico"
"${CS_ROOT}/scripts/doctor.sh" || true

say ""
if (( ${#FAILURES[@]} > 0 )); then
  fail "Instalação concluída com ${#FAILURES[@]} problema(s):"
  for item in "${FAILURES[@]}"; do
    say "    - ${item}"
  done
  say ""
  say "  Os ambientes que instalaram com sucesso já funcionam."
  exit 1
fi

ok "Instalação concluída."

# ---------------------------------------------------------------------------
# 11. Atalho de aplicativo (opcional)
# ---------------------------------------------------------------------------
if [[ -z "${ONLY}" ]] && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  step "11. Atalho de aplicativo"
  say "  Cria um ícone no menu e na área de trabalho para abrir o estúdio"
  say "  com um clique, sem precisar de terminal. Tudo dentro de ~/.local."
  say ""
  if confirm "Instalar o atalho?" s; then
    "${CS_ROOT}/scripts/install-desktop-entry.sh" || \
      warn "não foi possível instalar o atalho"
  else
    say "  Depois, se quiser:"
    say "      ${C_BOLD}./scripts/install-desktop-entry.sh${C_RESET}"
  fi
fi

say ""
say "  Próximos passos:"
say "      ${C_BOLD}./start.sh${C_RESET}          sobe backend + interface"
say "      ${C_BOLD}./scripts/doctor.sh${C_RESET} diagnóstico completo"
say ""
