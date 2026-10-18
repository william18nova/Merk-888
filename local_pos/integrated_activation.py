"""Explicit first activation. Requires the installation lock and owned engine.

Cloud reads only. No sale, restore, schema migration, or background worker is
started here. Existing pending operations and unrelated local data are refused.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
from uuid import uuid4

from .integrated_config import DATABASE, load_config
from .runtime import private_dir, save_json


class ActivationError(RuntimeError):
    pass


def backup_environment(config):
    if config["database"] != DATABASE or config["user"] != DATABASE:
        raise ActivationError("El respaldo solo admite la base privada integrada.")
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    env.update(PGHOST="127.0.0.1", PGPORT=str(config["port"]), PGDATABASE=DATABASE,
               PGUSER=DATABASE, PGPASSWORD=config["password"], PGCONNECT_TIMEOUT="5",
               PGSSLMODE="disable", PGAPPNAME="nova-local-pre-activation-backup",
               PGOPTIONS="-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000")
    return env


def backup_local(config, pg_bin):
    """Logical backup with native client tools; no native PostgreSQL server DLL.

The protected config accompanies the archive for recovery. Verification decodes
the entire archive without executing its SQL; it is not a restore rehearsal.
"""
    import psycopg2
    env = backup_environment(config)
    tools = {name: Path(pg_bin) / (name + (".exe" if os.name == "nt" else ""))
             for name in ("pg_dump", "pg_restore")}
    versions = {}
    for name, path in tools.items():
        if not path.is_absolute() or not path.is_file():
            raise ActivationError("No se encontró el cliente PostgreSQL para el respaldo.")
        result = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=15)
        match = re.search(r"PostgreSQL\) (\d+)", result.stdout)
        if result.returncode or not match:
            raise ActivationError("No se pudo verificar el cliente de respaldo.")
        versions[name] = int(match[1])
    connection = psycopg2.connect(host="127.0.0.1", port=config["port"], dbname=DATABASE,
        user=DATABASE, password=config["password"], connect_timeout=3,
        options="-c default_transaction_read_only=on -c statement_timeout=10000")
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_setting('server_version_num')::int, pg_database_size(current_database())")
            server_version, size = cursor.fetchone()
    finally:
        connection.close()
    if min(versions.values()) < server_version // 10000:
        raise ActivationError("El cliente de respaldo es anterior al servidor local.")
    root = private_dir(Path(config["data_dir"]) / "backups" /
        (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]))
    if shutil.disk_usage(root).free < max(size * 2, 100_000_000):
        raise ActivationError("No hay espacio suficiente para respaldar la base local.")
    report = {"format": "nova-integrated-local-backup-v1", "status": "started",
        "instance_id": config["instance_id"], "port": config["port"], "database": DATABASE,
        "created_at": datetime.now(timezone.utc).isoformat(), "source_bytes": size,
        "server_version_num": server_version}
    save_json(root / "config.before.json", config)
    save_json(root / "manifest.json", report)
    partial = root / "database.dump.partial"
    try:
        with (root / "pg_dump.log").open("xb") as log:
            result = subprocess.run([str(tools["pg_dump"]), "--format=custom", "--no-password",
                "--lock-wait-timeout=5s", "--file", str(partial)], env=env,
                stdout=subprocess.DEVNULL, stderr=log, timeout=300)
        if result.returncode:
            raise ActivationError("El respaldo local falló; la activación permanece sin cambios.")
        listing = subprocess.run([str(tools["pg_restore"]), "--list", str(partial)], capture_output=True, timeout=30)
        if listing.returncode or b"TABLE DATA" not in listing.stdout or b"local_pos_localnode" not in listing.stdout:
            raise ActivationError("El respaldo no contiene la identidad local esperada.")
        checked = subprocess.run([str(tools["pg_restore"]), "--file", os.devnull, str(partial)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
        if checked.returncode:
            raise ActivationError("El respaldo no superó la lectura completa.")
        digest = hashlib.sha256()
        with partial.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
        final = root / "database.dump"
        partial.rename(final)
        report.update(status="verified", file=final.name, bytes=final.stat().st_size,
            sha256=digest.hexdigest(), verification="pg_restore list + full decode; no restore executed")
        save_json(root / "manifest.json", report)
        return root
    except BaseException:
        report["status"] = "incomplete"
        save_json(root / "manifest.json", report)
        raise


def check_expected_scope(scope, expected):
    for key in ("user_id", "point_id", "branch_id"):
        if scope.get(key) != expected[key]:
            raise ActivationError("La autorización descargada no corresponde al usuario, caja y sucursal elegidos.")
    grant = scope.get("offline_sale", {})
    if (grant.get("required_turn") != expected["required_turn"]
            or grant.get("turn_id") != expected["turn_id"]):
        raise ActivationError("El turno cambió durante la descarga. No se activó la copia; vuelve a comprobarlo.")


def install_snapshot(expected):
    from django.conf import settings
    from django.db import transaction
    from django.utils import timezone as django_timezone
    from mainApp.models import TurnoCaja
    from pos_shared.replica import manifest
    from .integrated_auth import authorized_identity
    from .integrated_replica import capture, apply_snapshot, _assert_binding, _assert_clean
    from .models import ReplicaState
    from .replica import current_rows

    node_id = settings.LOCAL_CONFIG["instance_id"]
    _assert_binding(ReplicaState.objects.using("offline").filter(node_id=node_id).first(),
                    expected["user_id"], expected["point_id"])
    _assert_clean(node_id)
    identity, scope, rows, captured_at = capture(expected["user_id"], expected["point_id"])
    check_expected_scope(scope, expected)
    with transaction.atomic(using="offline"):
        state = apply_snapshot(identity, scope, rows, captured_at)
        if (state.blocked or state.expires_at <= django_timezone.now()
                or manifest(current_rows("offline")) != state.manifest
                or not authorized_identity(identity["actor"])):
            raise ActivationError("No pasó la verificación integral de la copia local.")
        if expected["required_turn"] and not TurnoCaja.objects.using("offline").filter(
            pk=expected["turn_id"], cajero_id=expected["user_id"], puntopago_id=expected["point_id"], estado="ABIERTO").exists():
            raise ActivationError("El turno autorizado no quedó disponible en la copia.")
        return {"user_id": expected["user_id"], "point_id": expected["point_id"],
            "branch_id": expected["branch_id"], "turn_id": expected["turn_id"],
            "snapshot_id": str(state.active_id), "manifest": state.manifest,
            "expires_at": state.expires_at.isoformat(), "counts": dict(Counter(row["entity"] for row in rows))}


def commit_activation(path, project, before):
    # The installation lock excludes runserver; this comparison also detects an
    # operator editing the file while the (potentially long) download ran.
    if load_config(path, project) != before:
        raise ActivationError("La configuración cambió durante la preparación. No se reemplazó.")
    save_json(path, {**before, "activation": "journal_preview"})


def verify_read_pages(user_id):
    """Render the original sale form against both databases, never submit it."""
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.db import connection, transaction
    from django.test import RequestFactory
    from mainApp.models import Usuario
    from mainApp.views import GenerarVentaView
    from .request_context import database_context

    result = []
    for alias, mode in (("default", "online"), ("offline", "offline")):
        with database_context(alias), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            request = RequestFactory().get("/generar_venta/", HTTP_HOST="127.0.0.1")
            request.user = Usuario.objects.get(pk=user_id)
            request.session = {}
            request._messages = FallbackStorage(request)
            request.nova_mode = mode
            response = GenerarVentaView.as_view()(request)
            if response.status_code != 200 or b'id="nova-integrated-status"' not in response.content:
                raise ActivationError(f"La vista original de ventas no pasó la comprobación {mode}.")
            result.append(mode)
    return result
