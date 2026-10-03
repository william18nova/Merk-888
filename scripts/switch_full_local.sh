#!/usr/bin/env bash
set -euo pipefail
PACKAGE="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd -- "$PACKAGE"
DATA="${1:-$HOME/.local/share/nova-pos-piloto}"
USERNAME="${2:-}"
echo 'Relevo: cierra y sincroniza el turno anterior y detén el POS con Ctrl+C.'
echo 'El siguiente cajero debe tener su turno abierto en la nube.'
if [ -z "$USERNAME" ]; then read -r -p 'Usuario del siguiente cajero en la nube: ' USERNAME; fi
if [ -z "$USERNAME" ]; then echo 'Falta el usuario.' >&2; exit 1; fi
"$PACKAGE/.venv/bin/python" -m local_pos.runtime --data-dir "$DATA" switch-user --username "$USERNAME"
echo 'Ahora inicia el POS con start_full_local.sh y el mismo directorio de datos.'
