#!/usr/bin/env bash
# Diagnóstico do Local Clone Studio.
set -uo pipefail
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
exec "$(backend_python)" -m apps.cli.main doctor "$@"
