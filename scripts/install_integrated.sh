#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ -e .novapos-runtime.json ]]; then
    echo 'Este proyecto ya tiene una instalación; no se reemplazó. Usa python manage.py runserver.' >&2
    exit 1
fi
if [[ ${EUID} -eq 0 ]]; then
    echo 'Instala y ejecuta como usuario normal, no root.' >&2
    exit 1
fi
if [[ $# -lt 1 ]]; then
    echo 'Uso: bash scripts/install_integrated.sh /usr/lib/postgresql/16/bin [puerto-local]' >&2
    exit 1
fi
python3 -c "import sys; assert (3,10) <= sys.version_info[:2] < (3,14), 'Usa Python 3.10 a 3.13'"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/prepare_integrated_install.py --pg-bin "$1" --port "${2:-55460}"
echo 'Preparación terminada. Falta vincular usuario, caja y turno con activate_integrated.py --apply.'
echo 'Para trabajar: source .venv/bin/activate y python manage.py runserver'
