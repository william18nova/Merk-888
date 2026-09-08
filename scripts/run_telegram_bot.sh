#!/usr/bin/env bash

# Ejecutor para una tarea Always-on de PythonAnywhere.
set -o pipefail

PROJECT_DIR="${TELEGRAM_PROJECT_DIR:-/home/Merk888/Merk-888}"
ENV_FILE="${TELEGRAM_ENV_FILE:-/home/Merk888/.telegram_bot.env}"
PYTHON_BIN="${TELEGRAM_PYTHON_BIN:-/home/Merk888/.virtualenvs/env/bin/python}"
LOG_DIR="${TELEGRAM_LOG_DIR:-/home/Merk888/logs}"
LOG_FILE="${LOG_DIR}/telegram_bot.log"

mkdir -p "$LOG_DIR" || exit 1
exec >>"$LOG_FILE" 2>&1

echo
echo "===== INICIO TELEGRAM BOT | $(date -u '+%Y-%m-%dT%H:%M:%SZ') ====="

if [[ ! -r "$ENV_FILE" ]]; then
    echo "ERROR: no se puede leer el archivo privado $ENV_FILE."
    exit 2
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
source_status=$?
set +a
if (( source_status != 0 )); then
    echo "ERROR: no se pudo cargar $ENV_FILE (código $source_status)."
    exit "$source_status"
fi

missing=()
for variable in TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET; do
    if [[ -z "${!variable:-}" ]]; then missing+=("$variable"); fi
done
if (( ${#missing[@]} > 0 )); then
    echo "ERROR: faltan variables privadas: ${missing[*]}."
    exit 2
fi
if [[ ! -d "$PROJECT_DIR" || ! -x "$PYTHON_BIN" ]]; then
    echo "ERROR: revisa TELEGRAM_PROJECT_DIR y TELEGRAM_PYTHON_BIN."
    exit 2
fi

cd "$PROJECT_DIR" || exit 2
# Diagnóstico de configuración sin consumir API ni mostrar secretos.
"$PYTHON_BIN" manage.py comprobar_ia_telegram --status || exit 2
exec "$PYTHON_BIN" -u manage.py procesar_telegram_bot
