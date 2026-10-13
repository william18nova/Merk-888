"""Apply only the reviewed 0044..0049 path after a verified private backup.

No --apply: verify the backup and print the plan without any schema changes.
No sales are created and the integrated local mode is not activated here.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
EXPECTED = ["0044_hybrid_pilot", "0045_hybrid_recovery", "0046_hybrid_reference_replica",
            "0047_hybrid_replica_sale_cursor", "0048_hybrid_business_operations", "0049_solicitudpos"]


def verified_backup(path, database):
    from local_pos.runtime import read_json
    report = read_json(path)
    if (report.get("format") != "nova-cloud-backup-v1" or report.get("status") != "verified"
            or report.get("file") != "database.dump"):
        raise RuntimeError("Se requiere un respaldo completo y verificado.")
    if (report.get("host") != str(database.get("HOST"))
            or report.get("port") != int(database.get("PORT", 5432))
            or report.get("database") != database.get("NAME")):
        raise RuntimeError("El respaldo no corresponde al destino configurado.")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(report["created_at"])).total_seconds()
    if not 0 <= age <= 7200:
        raise RuntimeError("El respaldo no es reciente. Crea uno nuevo antes de migrar.")
    archive = path.parent / report["file"]
    if archive.is_symlink() or archive.stat().st_size != report["bytes"]:
        raise RuntimeError("El archivo de respaldo cambió.")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    if digest.hexdigest() != report["sha256"]:
        raise RuntimeError("La huella del respaldo no coincide.")
    return report


def checked_plan(connection):
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan([("mainApp", EXPECTED[-1])])
    names = []
    for migration, backwards in plan:
        if backwards or migration.app_label != "mainApp" or migration.name not in EXPECTED:
            raise RuntimeError("El plan incluye cambios fuera de las migraciones autorizadas.")
        if any(type(op).__name__ not in {"CreateModel", "AddField", "AlterField"} for op in migration.operations):
            raise RuntimeError("La migración contiene operaciones no contempladas por esta autorización.")
        names.append(migration.name)
    if names and names != EXPECTED[EXPECTED.index(names[0]):]:
        raise RuntimeError("Cambió el orden esperado de las migraciones.")
    return names


def run(args):
    os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.settings"
    import django
    django.setup()
    from django.core.management import call_command
    from django.db import connections
    from local_pos.runtime import save_json
    connection = connections["default"]
    if connection.vendor != "postgresql" or "offline" in connections:
        raise RuntimeError("Usa únicamente la configuración normal de la nube.")
    report = verified_backup(args.backup_manifest, connection.settings_dict)
    original = connection.settings_dict
    connection.settings_dict = {**original, "OPTIONS": {**original.get("OPTIONS", {}),
        "connect_timeout": 10, "options": "-c lock_timeout=5000 -c statement_timeout=60000"}}
    locked = False
    try:
        if args.apply:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(781613840049)")
                locked = cursor.fetchone()[0]
                if not locked:
                    raise RuntimeError("Otra migración supervisada está en curso.")
        names = checked_plan(connection)
        print("Plan verificado: " + (", ".join(names) if names else "ya aplicado"), flush=True)
        if not args.apply:
            print("Respaldo válido. Solo comprobación: no se ejecutaron migraciones.", flush=True)
            return
        if names:
            call_command("migrate", "mainApp", EXPECTED[-1], interactive=False, verbosity=1)
        if checked_plan(connection):
            raise RuntimeError("Quedan migraciones pendientes; no activar el equipo.")
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('solicitudes_pos') IS NOT NULL")
            if not cursor.fetchone()[0]:
                raise RuntimeError("No se encontró la tabla de confirmaciones después de migrar.")
        save_json(args.backup_manifest.parent / "migration-result.json", {
            "completed_at": datetime.now(timezone.utc).isoformat(), "applied": names,
            "backup_sha256": report["sha256"], "target": EXPECTED[-1]})
        print("OK: esquema hasta 0049 verificado. Sin ventas de prueba; modo local todavía sin activar.", flush=True)
    finally:
        try:
            if locked:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(781613840049)")
        finally:
            connection.close()
            connection.settings_dict = original


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    try:
        run(parser.parse_args())
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else f"Migración no confirmada ({type(error).__name__}); revisar el estado antes de activar.", file=sys.stderr)
        sys.exit(1)
