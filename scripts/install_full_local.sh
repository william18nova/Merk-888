#!/usr/bin/env bash
set -euo pipefail
if [ "$#" -lt 3 ]; then
  echo 'Uso: bash install_full_local.sh /ruta/privada/datos /ruta/postgresql/bin usuario [wheelhouse]'
  exit 2
fi
PACKAGE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd -- "$PACKAGE"
if [ -f "$1/installation.json" ]; then
  echo 'Ya instalado. Usa start_full_local.sh, no inicialices otra vez.'
  exit 1
fi
python3 -m venv "$PACKAGE/.venv"
if [ "$#" -ge 4 ]; then
  "$PACKAGE/.venv/bin/python" -m pip install --no-index --find-links "$4" -r requirements_full_local.txt
else
  "$PACKAGE/.venv/bin/python" -m pip install -r requirements_full_local.txt
fi
exec "$PACKAGE/.venv/bin/python" -m local_pos.runtime --data-dir "$1" init --pg-bin "$2" --username "$3"
