#!/usr/bin/env bash
set -euo pipefail
PACKAGE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd -- "$PACKAGE"
exec "$PACKAGE/.venv/bin/python" -m local_pos.runtime --data-dir "${1:?Indica el directorio persistente de datos}" serve
