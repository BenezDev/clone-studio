#!/usr/bin/env bash
# Gerenciador de pesos de modelos.
set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
exec "$(backend_python)" "${CS_ROOT}/scripts/models.py" "$@"
