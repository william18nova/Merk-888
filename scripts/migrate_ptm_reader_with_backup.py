"""Apply the selected additive PTM migration after a verified recent full backup.

Default: reader 0051. --upgrade ptm-pool: cash-pool 0052, requires 0051 already applied.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.migrate_hybrid_with_backup import verified_backup

TARGET = ("mainApp", "0051_ptm_reader")
MODELS = {"CuentaLecturaPTM", "LectorPTM", "MovimientoLeidoPTM", "AuditoriaLecturaPTM"}


def checked_plan(connection, upgrade="ptm-reader"):
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.operations.models import CreateModel, AddConstraint
    from django.db.migrations.operations.fields import AddField
    target = ("mainApp", "0052_ptm_cash_pool") if upgrade == "ptm-pool" else TARGET
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan([target])
    if len(plan) > 1:
        raise RuntimeError("Hay otras migraciones pendientes; no se aplicarán automáticamente.")
    for migration, backwards in plan:
        if backwards or (migration.app_label, migration.name) != target:
            raise RuntimeError("El plan no coincide con la migración PTM autorizada.")
        if upgrade == "ptm-pool":
            expected = {"LecturaDiariaPTM", "BolsaEfectivoPTM", "AjusteBolsaPTM", "CorteBolsaPTM", "AuditoriaBolsaPTM", "ParticipacionBolsaPTM"}
            creates = [op for op in migration.operations if type(op) is CreateModel]
            fields = [op for op in migration.operations if type(op) is AddField]
            constraints = [op for op in migration.operations if type(op) is AddConstraint]
            if (len(migration.operations) != 8 or {op.name for op in creates} != expected
                    or len(fields) != 1 or fields[0].model_name != "operacionptm"
                    or fields[0].name != "bolsa" or not fields[0].field.null
                    or len(constraints) != 1 or constraints[0].model_name != "cortebolsaptm"):
                raise RuntimeError("La migración de bolsa cambió; requiere otra revisión.")
        elif (len(migration.operations) != 4
                  or any(type(op) is not CreateModel for op in migration.operations)
                  or {op.name for op in migration.operations} != MODELS):
            raise RuntimeError("La migración cambió; requiere otra revisión.")
    return executor, plan


def run(args):
    os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.settings"
    import django
    django.setup()
    from django.db import connections
    from local_pos.runtime import save_json
    connection = connections["default"]
    upgrade = args.upgrade
    target = ("mainApp", "0052_ptm_cash_pool") if upgrade == "ptm-pool" else TARGET
    models = ({"LecturaDiariaPTM", "BolsaEfectivoPTM", "AjusteBolsaPTM", "CorteBolsaPTM", "AuditoriaBolsaPTM", "ParticipacionBolsaPTM", "OperacionPTM"}
              if upgrade == "ptm-pool" else MODELS)
    if connection.vendor != "postgresql" or "offline" in connections:
        raise RuntimeError("Se requiere la configuración normal de PostgreSQL online.")
    report = verified_backup(args.backup_manifest, connection.settings_dict)
    connection.settings_dict["OPTIONS"] = {**connection.settings_dict.get("OPTIONS", {}),
        "connect_timeout": 10, "options": "-c lock_timeout=5000 -c statement_timeout=60000"}
    locked = False
    try:
        if args.apply:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(781613840049)")
                locked = cursor.fetchone()[0]
                if not locked:
                    raise RuntimeError("Otra migración supervisada está en curso.")
        executor, plan = checked_plan(connection, upgrade)
        print("Plan: " + (target[1] if plan else "ya aplicado"), flush=True)
        if not args.apply:
            print("Respaldo verificado. Solo revisión; sin cambios.")
            return
        if plan:
            executor.migrate([target], plan=plan)
        if checked_plan(connection, upgrade)[1]:
            raise RuntimeError("La migración sigue pendiente.")
        state = executor.loader.project_state([target])
        for name in sorted(models):
            model = state.apps.get_model("mainApp", name)
            with connection.cursor() as cursor:
                columns = connection.introspection.get_table_description(cursor, model._meta.db_table)
            if {col.name for col in columns} != {field.column for field in model._meta.fields}:
                raise RuntimeError("No coincide el esquema de una tabla PTM.")
        save_json(args.backup_manifest.parent / ("migration-" + target[1] + "-result.json"), {
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "target": list(target), "applied": bool(plan), "verified_tables": sorted(models),
            "backup_sha256": report["sha256"],
        })
        print("OK: " + target[1] + " verificada. No se crearon bolsas activas ni operaciones de caja.")
    finally:
        try:
            if locked:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(781613840049)")
        finally:
            connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--upgrade", choices=("ptm-reader", "ptm-pool"), default="ptm-reader")
    try:
        run(parser.parse_args())
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else
              f"Migración no confirmada ({type(error).__name__}); revisar el estado.", file=sys.stderr)
        sys.exit(1)
