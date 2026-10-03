#!/usr/bin/env bash
# Solo datos ficticios. Requiere PostgreSQL instalado y un usuario no root.
set -euo pipefail
if [ "$(id -u)" = 0 ]; then
  echo 'Ejecuta esta prueba con un usuario Linux sin privilegios, no como root.' >&2
  exit 1
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${HYBRID_TEST_PYTHON:-python3}"
PG_BIN="${HYBRID_TEST_PG_BIN:-$(pg_config --bindir)}"
PORT="${HYBRID_TEST_POSTGRES_PORT:-55432}"
if [[ ! "$PORT" =~ ^[0-9]+$ ]] || (( PORT < 1024 || PORT > 65535 )); then
  echo 'Puerto inválido.' >&2; exit 1
fi
if "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PORT" >/dev/null 2>&1; then
  echo 'El puerto ya tiene una base. No se utilizará una base preexistente.' >&2
  exit 1
fi
umask 077
RUN="$(mktemp -d "${TMPDIR:-/tmp}/novapos-pg-test.XXXXXXXX")"
STARTED=0
cleanup() {
  status=$?
  trap - EXIT
  if [ "$STARTED" = 1 ]; then
    "$PG_BIN/pg_ctl" -D "$RUN/data" -m fast -w -t 30 stop || status=1
  fi
  rm -f -- "$RUN/init-password.txt"
  echo "Datos ficticios y log conservados en: $RUN"
  exit "$status"
}
trap cleanup EXIT
export HYBRID_TEST_POSTGRES_PASSWORD
HYBRID_TEST_POSTGRES_PASSWORD="$("$PYTHON" -c 'import secrets; print(secrets.token_hex(32))')"
printf '%s' "$HYBRID_TEST_POSTGRES_PASSWORD" > "$RUN/init-password.txt"
"$PG_BIN/initdb" -D "$RUN/data" -U nova_hybrid_ci -A scram-sha-256 -E UTF8 --locale=C --pwfile="$RUN/init-password.txt"
rm -f -- "$RUN/init-password.txt"
"$PG_BIN/pg_ctl" -D "$RUN/data" -l "$RUN/postgres.log" -o "-h 127.0.0.1 -p $PORT -k $RUN -c max_connections=30" -w -t 30 start
STARTED=1
export PGPASSWORD="$HYBRID_TEST_POSTGRES_PASSWORD"
"$PG_BIN/createdb" -h 127.0.0.1 -p "$PORT" -U nova_hybrid_ci nova_hybrid_ci
export HYBRID_TEST_POSTGRES=yes-local-disposable
export HYBRID_TEST_POSTGRES_PORT="$PORT"
cd "$ROOT"
tests=(mainApp.test_hybrid mainApp.test_hybrid_recovery mainApp.test_hybrid_postgres mainApp.test_hybrid_migration mainApp.test_hybrid_acceptance mainApp.test_cash_receipt mainApp.test_ptm)
if (( $# )); then tests=("$@"); fi
"$PYTHON" -B manage.py test "${tests[@]}" --settings=NovaSoft.hybrid_test_postgres_settings --noinput
