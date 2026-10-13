"""Back up and activate the normal runserver's authorized offline sale copy.

Explicit user, point and current shift IDs are mandatory. Without --apply only
the cloud prerequisites are checked. No cloud write or real test sale is made.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(args):
    from local_pos.integrated_config import config_path, load_config, register_location
    from local_pos.runtime import installation_lock, save_json, Postgres
    from local_pos.integrated_activation import (backup_local, install_snapshot,
        commit_activation, verify_read_pages, ActivationError)
    path = args.config or config_path(ROOT)
    config = load_config(path, ROOT)
    os.environ["NOVA_INTEGRATED_CONFIG"] = str(path)
    os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.integrated_activation_settings"
    import django
    django.setup()
    from django.db import connections
    from mainApp.services.hybrid_activation_checks import inspect_source
    from local_pos.replica import ReplicaError
    expected = inspect_source(user_id=args.usuario_id, point_id=args.caja_id)
    if not expected["required_turn"] or expected["turn_id"] != args.turno_id:
        raise ActivationError("El turno activo no coincide con el turno autorizado expresamente.")
    print(f"Confirmado: {expected['user_name']}, {expected['point_name']}, turno {expected['turn_id']}.", flush=True)
    if not args.apply:
        print("Comprobación solamente. No se respaldó, descargó ni activó una copia.")
        return
    from local_pos.integrated_assets import load_assets
    load_assets(ROOT)
    with installation_lock(Path(config["data_dir"])):
        if "backend" in config:
            from local_pos.wsl_postgres import WslPostgres
            engine = WslPostgres
        else:
            engine = Postgres
        postgres = engine(config["data_dir"], config["pg_bin"], config)
        postgres.start()
        try:
            backup = backup_local(config, args.pg_bin)
            print(f"Respaldo local verificado: {backup}", flush=True)
            print("Descargando y verificando la copia autorizada (nube en modo solo lectura)...", flush=True)
            try:
                result = install_snapshot(expected)
            except ReplicaError as exc:
                raise ActivationError(str(exc)) from None
            result["read_pages_verified"] = verify_read_pages(expected["user_id"])
            result.update(status="copy_verified", backup=str(backup),
                created_at=datetime.now(timezone.utc).isoformat())
            save_json(backup / "activation-result.json", result)
            register_location(path, ROOT)
            commit_activation(path, ROOT, config)
            result["status"] = "activated"
            save_json(backup / "activation-result.json", result)
            print(f"ACTIVADO: journal_preview; turno {result['turn_id']}; vigencia {result['expires_at']}.", flush=True)
            print(f"Registros autorizados: {result['counts']}", flush=True)
        finally:
            connections.close_all()
            postgres.stop()
    print("Motor privado detenido. El próximo python manage.py runserver lo iniciará automáticamente.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--pg-bin", type=Path, required=True, help="Clientes pg_dump/pg_restore compatibles.")
    parser.add_argument("--usuario-id", type=int, required=True)
    parser.add_argument("--caja-id", type=int, required=True)
    parser.add_argument("--turno-id", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    try:
        args = parser.parse_args()
        if min(args.usuario_id, args.caja_id, args.turno_id) < 1:
            raise RuntimeError("Los IDs deben ser positivos.")
        run(args)
    except Exception as exc:
        # Deliberate validation messages only: never emit raw DB/process errors.
        print(str(exc) if isinstance(exc, RuntimeError) else
              f"Activación interrumpida ({type(exc).__name__}). Revisa el estado antes de reintentar; no restaures ni elimines datos.", file=sys.stderr)
        from django.db import DatabaseError
        if isinstance(exc, DatabaseError):
            from mainApp.services.hybrid_activation_checks import database_failure_reason
            code = getattr(exc.__cause__, "pgcode", None)
            if code == "57014":
                print("Una consulta excedió su tiempo permitido. La copia incompleta no se activa.", file=sys.stderr)
            elif code == "55P03":
                print("La copia encontró un bloqueo de base de datos. No se forzó ni se eliminó ningún bloqueo.", file=sys.stderr)
            else:
                print(database_failure_reason(exc), file=sys.stderr)
        sys.exit(1)
