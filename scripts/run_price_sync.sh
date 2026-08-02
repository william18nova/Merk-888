#!/usr/bin/env bash

# Ejecutor estable para la tarea Daily de PythonAnywhere.
# Centraliza la carga del entorno y captura también los errores que ocurran
# antes de iniciar Django (archivo .env, rutas, virtualenv, etc.).

set -o pipefail

PROJECT_DIR="${PRICE_SYNC_PROJECT_DIR:-/home/Merk888/Merk-888}"
ENV_FILE="${PRICE_SYNC_ENV_FILE:-/home/Merk888/.price_sync.env}"
PYTHON_BIN="${PRICE_SYNC_PYTHON_BIN:-/home/Merk888/.virtualenvs/env/bin/python}"
LOG_DIR="${PRICE_SYNC_LOG_DIR:-/home/Merk888/logs}"
LOG_FILE="${LOG_DIR}/precios_plaza.log"

mkdir -p "$LOG_DIR" || exit 1
exec >>"$LOG_FILE" 2>&1

run_sync() {
    local source_status
    local missing=()
    local variable

    if [[ ! -r "$ENV_FILE" ]]; then
        echo "ERROR: no se puede leer el archivo privado $ENV_FILE."
        return 2
    fi

    # Exporta también asignaciones válidas que accidentalmente no incluyan
    # la palabra `export` dentro del archivo privado.
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    source_status=$?
    set +a
    if (( source_status != 0 )); then
        echo "ERROR: no se pudo cargar $ENV_FILE (código $source_status)."
        return "$source_status"
    fi

    for variable in \
        PRICE_SYNC_SOURCE_HOST \
        PRICE_SYNC_SOURCE_PORT \
        PRICE_SYNC_SOURCE_NAME \
        PRICE_SYNC_SOURCE_USER \
        PRICE_SYNC_SOURCE_PASSWORD
    do
        if [[ -z "${!variable:-}" ]]; then
            missing+=("$variable")
        fi
    done
    if (( ${#missing[@]} > 0 )); then
        echo "ERROR: faltan variables privadas: ${missing[*]}."
        return 2
    fi

    if [[ ! -d "$PROJECT_DIR" ]]; then
        echo "ERROR: no existe el proyecto $PROJECT_DIR."
        return 2
    fi
    if [[ ! -x "$PYTHON_BIN" ]]; then
        echo "ERROR: no se puede ejecutar el Python del entorno $PYTHON_BIN."
        return 2
    fi

    cd "$PROJECT_DIR" || return 2
    "$PYTHON_BIN" -u manage.py actualizar_precios_plaza --apply "$@"
}

started_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
started_bogota="$(TZ=America/Bogota date '+%Y-%m-%d %H:%M:%S %Z')"
commit="$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || printf 'desconocido')"

echo
echo "===== INICIO PRICE SYNC | UTC $started_utc | Bogotá $started_bogota | commit $commit ====="
run_sync "$@"
status=$?
finished_utc="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
if (( status == 0 )); then
    echo "===== FIN PRICE SYNC | OK | UTC $finished_utc ====="
else
    echo "===== FIN PRICE SYNC | ERROR $status | UTC $finished_utc ====="
fi

exit "$status"
