#!/usr/bin/env bash
#
# Empacota o projeto para instalar noutra máquina (tipicamente Windows).
#
# Existe porque zip feito à mão sai incompleto: a primeira versão deste pacote
# foi montada manualmente e esqueceu o COMO-RODAR.md e os 50 roteiros da
# biblioteca. Aqui a lista de inclusão é explícita e conferida no final.
#
# NÃO entram: ambientes virtuais, node_modules, pesos de modelo, cache, logs,
# e principalmente `data/` — voz, vídeos e projetos são seus, não do pacote.

set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

NOME="CloneStudio"
DESTINO="${CS_ROOT}/exports/${NOME}-para-Windows.zip"
TEMP="$(mktemp -d)"
trap 'rm -rf "${TEMP}"' EXIT

have zip || die "O comando 'zip' não está instalado. Rode: sudo apt install zip"

step "Empacotando o Local Clone Studio"

STAGE="${TEMP}/${NOME}"
mkdir -p "${STAGE}"

# Diretórios de código e conteúdo que precisam ir.
DIRETORIOS=(
  apps/api apps/cli
  core services scripts
  config requirements assets docs tests
  workflows
)
# `apps/web` entra sem node_modules nem dist.
ARQUIVOS=(
  install.sh start.sh stop.sh dev.sh
  install.ps1 start.ps1 stop.ps1
  install.bat start.bat stop.bat doctor.bat
  README.md COMO-RODAR.md LICENSE pytest.ini .gitignore .gitattributes
)

for d in "${DIRETORIOS[@]}"; do
  [[ -d "${CS_ROOT}/${d}" ]] || { warn "faltando: ${d}"; continue; }
  mkdir -p "${STAGE}/$(dirname "${d}")"
  cp -r "${CS_ROOT}/${d}" "${STAGE}/${d}"
done

mkdir -p "${STAGE}/apps/web"
for item in src index.html package.json tsconfig.json vite.config.ts; do
  [[ -e "${CS_ROOT}/apps/web/${item}" ]] && \
    cp -r "${CS_ROOT}/apps/web/${item}" "${STAGE}/apps/web/${item}"
done

for f in "${ARQUIVOS[@]}"; do
  [[ -f "${CS_ROOT}/${f}" ]] && cp "${CS_ROOT}/${f}" "${STAGE}/${f}" \
    || warn "faltando: ${f}"
done

# Limpeza do que nunca deve viajar.
find "${STAGE}" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null
find "${STAGE}" -type d -name ".pytest_cache" -prune -exec rm -rf {} + 2>/dev/null
find "${STAGE}" -type d -name "node_modules" -prune -exec rm -rf {} + 2>/dev/null
find "${STAGE}" -type f -name "*.pyc" -delete 2>/dev/null

# Estrutura vazia dos diretórios que o usuário vai preencher.
mkdir -p "${STAGE}/data/identity/"{voice,templates,photos,videos,metadata}
mkdir -p "${STAGE}/data/assets/broll" "${STAGE}/models" "${STAGE}/projects"
mkdir -p "${STAGE}/exports" "${STAGE}/logs" "${STAGE}/cache"
find "${STAGE}/data" "${STAGE}/models" "${STAGE}/projects" \
     "${STAGE}/exports" "${STAGE}/logs" "${STAGE}/cache" \
     -type d -exec touch {}/.gitkeep \;

# --- conferência ------------------------------------------------------------
# Um pacote incompleto só é descoberto na casa do usuário. Confere aqui.
step "Conferindo o conteúdo"
OBRIGATORIOS=(
  "install.ps1" "start.ps1" "stop.ps1"
  "install.bat" "start.bat" "stop.bat" "doctor.bat"
  "scripts/doctor.ps1" "scripts/models.ps1" "scripts/clone-studio.ps1" "scripts/clone-studio.bat"
  "scripts/launch.py" "scripts/models.py"
  "COMO-RODAR.md" "README.md" "LICENSE"
  "docs/WINDOWS.md" "docs/TROUBLESHOOTING.md"
  "config/default.yaml" "config/model_registry.yaml"
  "requirements/backend.txt" "requirements/qwen-tts.txt"
  "requirements/whisper.txt" "requirements/musetalk.txt"
  "apps/web/package.json" "apps/web/src/App.tsx"
  "apps/api/app/main.py" "apps/cli/main.py"
)
FALHAS=0
for f in "${OBRIGATORIOS[@]}"; do
  if [[ ! -e "${STAGE}/${f}" ]]; then
    fail "ausente no pacote: ${f}"
    FALHAS=$((FALHAS + 1))
  fi
done

ROTEIROS="$(find "${STAGE}/assets/scripts" -name "*.yaml" 2>/dev/null | wc -l)"
if (( ROTEIROS < 5 )); then
  fail "biblioteca de roteiros incompleta: ${ROTEIROS} arquivo(s)"
  FALHAS=$((FALHAS + 1))
else
  detail "biblioteca de roteiros: ${ROTEIROS} arquivos"
fi

# Nada de dado pessoal pode viajar junto.
VAZAMENTO="$(find "${STAGE}/data" -type f ! -name ".gitkeep" 2>/dev/null | wc -l)"
if (( VAZAMENTO > 0 )); then
  fail "o pacote contém ${VAZAMENTO} arquivo(s) em data/ — dado pessoal não viaja"
  find "${STAGE}/data" -type f ! -name ".gitkeep" | sed 's/^/    /'
  FALHAS=$((FALHAS + 1))
fi

(( FALHAS > 0 )) && die "${FALHAS} problema(s). Pacote não gerado."

# --- zip --------------------------------------------------------------------
mkdir -p "$(dirname "${DESTINO}")"
rm -f "${DESTINO}"
( cd "${TEMP}" && zip -qr "${DESTINO}" "${NOME}" )

TAMANHO="$(du -h "${DESTINO}" | cut -f1)"
ARQUIVOS_TOTAL="$(unzip -l "${DESTINO}" | tail -1 | awk '{print $2}')"

say ""
ok "Pacote gerado."
say "  arquivo : ${DESTINO}"
say "  tamanho : ${TAMANHO}  ·  ${ARQUIVOS_TOTAL} arquivos"
say ""
say "  Na máquina destino, descompacte em ${C_BOLD}C:\\CloneStudio${C_RESET} e leia"
say "  o ${C_BOLD}COMO-RODAR.md${C_RESET}."
say ""
