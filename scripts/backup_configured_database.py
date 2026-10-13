"""Explicit, read-only pg_dump of the configured cloud DB into a private folder.

Never restores or migrates. Credentials go only to the child environment, not
arguments, manifests, stdout or repository files. Requires an explicit target.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(args):
    os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.settings"
    import django
    django.setup()
    from django.db import connections
    from local_pos.runtime import private_dir, save_json

    cloud = connections["default"]
    if cloud.vendor != "postgresql":
        raise RuntimeError("Se requiere PostgreSQL.")
    params = cloud.get_connection_params()
    if (str(params.get("host")) != args.expect_host or int(params.get("port", 5432)) != args.expect_port
            or params.get("dbname") != args.expect_name):
        raise RuntimeError("El destino configurado no coincide con el destino autorizado.")
    tools = {name: args.pg_bin / (name + (".exe" if os.name == "nt" else ""))
             for name in ("pg_dump", "pg_restore")}
    versions = {}
    for name, path in tools.items():
        if not path.is_absolute() or not path.is_file():
            raise RuntimeError("No se encontró el cliente PostgreSQL indicado.")
        version = subprocess.run([str(path), "--version"], capture_output=True, text=True,
                                 check=True, timeout=15).stdout.strip()
        match = re.search(r"PostgreSQL\) (\d+)", version)
        if not match:
            raise RuntimeError("No se pudo verificar la versión del cliente.")
        versions[name] = (int(match[1]), version)

    import psycopg2
    connection = psycopg2.connect(**{**params, "connect_timeout": 10,
        "options": "-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=5000"})
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::int, pg_database_size(current_database())")
            server_version, size = cursor.fetchone()
        connection.rollback()
    finally:
        connection.close()
    if min(value[0] for value in versions.values()) < server_version // 10000:
        raise RuntimeError("El cliente de respaldo es más antiguo que el servidor.")
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "NovaPOS/backups/aiven"
    root = private_dir(base / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]))
    if shutil.disk_usage(root).free < max(size * 2, 500_000_000):
        raise RuntimeError("No hay suficiente espacio libre para el respaldo.")
    partial, final = root / "database.dump.partial", root / "database.dump"
    report = {"format": "nova-cloud-backup-v1", "status": "started",
              "created_at": datetime.now(timezone.utc).isoformat(),
              "host": args.expect_host, "port": args.expect_port, "database": args.expect_name,
              "source_bytes": size, "server_version_num": server_version,
              "tools": {name: value[1] for name, value in versions.items()}}
    save_json(root / "manifest.json", report)
    env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
    mapping = {"host": "PGHOST", "port": "PGPORT", "dbname": "PGDATABASE", "user": "PGUSER",
               "password": "PGPASSWORD", "hostaddr": "PGHOSTADDR", "sslmode": "PGSSLMODE",
               "sslrootcert": "PGSSLROOTCERT", "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
               "sslcrl": "PGSSLCRL", "sslcrldir": "PGSSLCRLDIR", "channel_binding": "PGCHANNELBINDING",
               "gssencmode": "PGGSSENCMODE", "sslpassword": "PGSSLPASSWORD"}
    for key, value in params.items():
        if key in mapping and value is not None:
            env[mapping[key]] = str(value)
        elif key not in {"cursor_factory", "options", "connect_timeout", "client_encoding"}:
            raise RuntimeError("La conexión usa una opción no contemplada por este respaldo; no se ignorará.")
    env["PGCONNECT_TIMEOUT"] = "10"
    env["PGAPPNAME"] = "novapos-authorized-pre-hybrid-backup"
    env["PGOPTIONS"] = "-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000"
    print(f"Respaldo privado: {root}", flush=True)
    try:
        # Private progress log contains object names, never connection secrets.
        # Slow WAN copies need a longer bound than local test snapshots.
        with (root / "pg_dump.log").open("xb") as log:
            result = subprocess.run([str(tools["pg_dump"]), "--format=custom", "--no-password", "--verbose",
                "--lock-wait-timeout=5s", "--file", str(partial)], env=env,
                stdout=subprocess.DEVNULL, stderr=log, timeout=args.timeout)
        if result.returncode:
            raise RuntimeError(f"pg_dump no terminó correctamente (código {result.returncode}); no se permite migrar.")
        print("Descarga terminada. Verificando catálogo y lectura completa del archivo...", flush=True)
        listing = subprocess.run([str(tools["pg_restore"]), "--list", str(partial)],
                                 capture_output=True, timeout=60)
        if listing.returncode or b"TABLE DATA" not in listing.stdout or b"django_migrations" not in listing.stdout:
            raise RuntimeError("El respaldo no contiene el catálogo de tablas esperado.")
        # Decode every block without connecting to any database or executing SQL.
        checked = subprocess.run([str(tools["pg_restore"]), "--file", os.devnull, str(partial)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=300)
        if checked.returncode:
            raise RuntimeError("El respaldo no superó la lectura completa; no se permite migrar.")
        digest = hashlib.sha256()
        with partial.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        partial.rename(final)
        report.update(status="verified", file=final.name, bytes=final.stat().st_size,
                      sha256=digest.hexdigest(), verified_at=datetime.now(timezone.utc).isoformat(),
                      verification="pg_restore list + full decode; no restore executed")
        save_json(root / "manifest.json", report)
        print(f"OK: {final}; {report['bytes']} bytes; SHA256 {report['sha256']}", flush=True)
        print("Sin escrituras en Aiven. No se ejecutó una restauración ni una migración.", flush=True)
    except BaseException:
        report["status"] = "incomplete"
        save_json(root / "manifest.json", report)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--expect-host", required=True)
    parser.add_argument("--expect-port", type=int, required=True)
    parser.add_argument("--expect-name", required=True)
    parser.add_argument("--timeout", type=int, default=3600,
                        help="Tiempo máximo de descarga en segundos; por defecto, una hora.")
    try:
        args = parser.parse_args()
        if not 60 <= args.timeout <= 7200:
            raise RuntimeError("El tiempo de descarga debe estar entre 60 y 7200 segundos.")
        run(args)
    except Exception as error:
        # Driver/process exception messages can contain credentials or queries.
        print(str(error) if isinstance(error, RuntimeError) else f"Respaldo incompleto ({type(error).__name__}); no migrar.", file=sys.stderr)
        sys.exit(1)
