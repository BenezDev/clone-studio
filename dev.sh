#!/usr/bin/env bash
#
# Modo desenvolvimento: hot reload no backend e no frontend.
set -uo pipefail
export CLONE_STUDIO_DEV=1
exec "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/start.sh" --dev "$@"
