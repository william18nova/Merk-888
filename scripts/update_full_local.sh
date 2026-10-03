#!/usr/bin/env bash
set -euo pipefail
PACKAGE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd -- "$PACKAGE"
DATA="${1:?Indica el directorio de datos}"
PREVIOUS="${2:?Indica el paquete anterior}"
BACKUP="${3:?Indica una carpeta nueva junto al directorio de datos}"
PYTHON="${4:-python3}"
WHEELHOUSE="${5:-}"
echo 'Detén el POS. Conserva el paquete anterior y no borres datos.'
if [ ! -x "$PACKAGE/.venv/bin/python" ]; then "$PYTHON" -m venv "$PACKAGE/.venv"; fi
if [ -n "$WHEELHOUSE" ]; then
  "$PACKAGE/.venv/bin/python" -m pip install --no-index --find-links "$WHEELHOUSE" -r requirements_full_local.txt
else
  "$PACKAGE/.venv/bin/python" -m pip install -r requirements_full_local.txt
fi
"$PACKAGE/.venv/bin/python" -m local_pos.runtime --data-dir "$DATA" upgrade --previous-package "$PREVIOUS" --backup-dir "$BACKUP"
echo 'Actualización confirmada. Usa start_full_local.sh de ESTE paquete y el mismo directorio de datos.'
