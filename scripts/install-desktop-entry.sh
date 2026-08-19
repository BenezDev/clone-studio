#!/usr/bin/env bash
#
# Instala o Local Clone Studio como aplicativo do sistema.
#
# Depois disto ele aparece no menu de aplicativos, pode ser fixado no painel e
# ganha um ícone na área de trabalho — clicar abre a interface no navegador.
#
# Tudo é feito dentro de ~/.local: nenhum `sudo`, nada fora do diretório do
# usuário. Para desfazer, rode com --uninstall.

set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

APP_ID="local-clone-studio"
APPS_DIR="${HOME}/.local/share/applications"
ICONS_DIR="${HOME}/.local/share/icons/hicolor/scalable/apps"
DESKTOP_FILE="${APPS_DIR}/${APP_ID}.desktop"
ICON_SOURCE="${CS_ROOT}/assets/clone-studio.svg"
ICON_TARGET="${ICONS_DIR}/${APP_ID}.svg"
LAUNCHER="${CS_ROOT}/scripts/app-launcher.sh"

UNINSTALL=0
NO_DESKTOP_ICON=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)  UNINSTALL=1; shift ;;
    --no-desktop) NO_DESKTOP_ICON=1; shift ;;
    -h|--help)
      say "uso: install-desktop-entry.sh [--uninstall] [--no-desktop]"; exit 0 ;;
    *) die "argumento desconhecido: $1" ;;
  esac
done

desktop_dir() {
  if have xdg-user-dir; then
    xdg-user-dir DESKTOP 2>/dev/null
  else
    printf '%s/Desktop' "${HOME}"
  fi
}

refresh_caches() {
  have update-desktop-database && \
    update-desktop-database "${APPS_DIR}" >/dev/null 2>&1 || true
  have gtk-update-icon-cache && \
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" \
      >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Desinstalação
# ---------------------------------------------------------------------------
if (( UNINSTALL )); then
  step "Removendo o atalho"
  rm -f "${DESKTOP_FILE}" "${ICON_TARGET}"
  rm -f "$(desktop_dir)/${APP_ID}.desktop"
  refresh_caches
  ok "Atalho removido. O projeto em si não foi tocado."
  exit 0
fi

# ---------------------------------------------------------------------------
# Instalação
# ---------------------------------------------------------------------------
step "Instalando o Local Clone Studio como aplicativo"

[[ -f "${ICON_SOURCE}" ]] || die "Ícone não encontrado: ${ICON_SOURCE}"
[[ -f "${LAUNCHER}" ]] || die "Lançador não encontrado: ${LAUNCHER}"

chmod +x "${LAUNCHER}" "${CS_ROOT}/start.sh" "${CS_ROOT}/stop.sh" 2>/dev/null || true

mkdir -p "${APPS_DIR}" "${ICONS_DIR}"
cp -f "${ICON_SOURCE}" "${ICON_TARGET}"
detail "ícone → ${ICON_TARGET}"

# O caminho do projeto tem espaço e acento. Na chave Exec, a especificação
# freedesktop manda envolver em aspas duplas o argumento que contém espaços.
write_desktop() { # destino
  cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Version=1.1
Name=Local Clone Studio
GenericName=Estúdio de vídeos verticais
Comment=Gera vídeos verticais com sua voz e seu rosto, 100% local
Exec="${LAUNCHER}"
Icon=${APP_ID}
Terminal=false
Categories=AudioVideo;Video;AudioVideoEditing;
Keywords=vídeo;video;tiktok;reels;shorts;voz;clone;ia;
StartupNotify=true
SingleMainWindow=true
Actions=Parar;Diagnostico;

[Desktop Action Parar]
Name=Parar
Exec="${LAUNCHER}" --stop

[Desktop Action Diagnostico]
Name=Diagnóstico
Exec="${LAUNCHER}" --doctor
EOF
  chmod +x "$1"
}

write_desktop "${DESKTOP_FILE}"
detail "atalho → ${DESKTOP_FILE}"

# Validação: um .desktop malformado é ignorado em silêncio pelo menu, que é o
# pior modo de falha possível — o usuário não descobre por quê.
if have desktop-file-validate; then
  if desktop-file-validate "${DESKTOP_FILE}" 2>/dev/null; then
    detail "desktop-file-validate: ok"
  else
    warn "desktop-file-validate reclamou:"
    desktop-file-validate "${DESKTOP_FILE}" 2>&1 | sed 's/^/    /'
  fi
fi

# --- ícone na área de trabalho ---------------------------------------------
if (( ! NO_DESKTOP_ICON )); then
  DESK="$(desktop_dir)"
  if [[ -d "${DESK}" ]]; then
    write_desktop "${DESK}/${APP_ID}.desktop"
    # O Cinnamon/Nautilus só executa lançadores marcados como confiáveis.
    if have gio; then
      gio set "${DESK}/${APP_ID}.desktop" metadata::trusted true 2>/dev/null || true
      # Alguns ambientes ainda usam a chave antiga.
      gio set "${DESK}/${APP_ID}.desktop" metadata::caja-trusted true 2>/dev/null || true
    fi
    detail "ícone na área de trabalho → ${DESK}"
  else
    warn "pasta da área de trabalho não encontrada; só o menu foi configurado"
  fi
fi

refresh_caches

say ""
ok "Pronto."
say ""
say "  Procure por ${C_BOLD}Local Clone Studio${C_RESET} no menu de aplicativos,"
say "  ou use o ícone na área de trabalho."
say ""
say "  ${C_DIM}Clique com o botão direito no ícone para Parar ou rodar o Diagnóstico.${C_RESET}"
say "  ${C_DIM}Para remover: ./scripts/install-desktop-entry.sh --uninstall${C_RESET}"
say ""
