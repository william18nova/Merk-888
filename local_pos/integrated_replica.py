"""Read-only cloud snapshot -> atomic private reference copy, no cloud writes.

Uses the normal Django database connection, not an alternate POS/API. Business
tables are allowlisted by the existing replica protocol; identities are local
only and never exposed in the reference status API.
"""
from collections import Counter
from datetime import timedelta
import hashlib
from uuid import UUID, uuid4

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

from mainApp.models import PuntosPago, Usuario
from mainApp.permissions import user_can_access_url_name
from mainApp.services.hybrid_replica import collect
from pos_shared.replica import VERSION, PAGE_SIZE, manifest, row_key
from .integrated_auth import collect_identity, apply_identity, cloud_fingerprint
from .models import (LocalNode, LocalCommand, LocalSaleSession, IntegratedIdentity,
    ReplicaState, ReplicaRow, ReplicaTransfer, ReplicaPage)
from .replica import ReplicaError, ReplicaScopeError, current_rows, validate_rows, activate
from .request_context import offline_request
from .integrated_policy import authorization_expiry, CLOUD_CONNECT_TIMEOUT


SOURCE_PREFIX = "django-db:"


def authorize(snapshot, point_id):
    actor, employee = snapshot["actor"], snapshot["employee"]
    if (not actor.is_active or not employee or not employee.sucursalid_id
            or not user_can_access_url_name(actor, "generar_venta")):
        raise ReplicaScopeError("No tienes autorización para preparar la copia de esta sucursal.")
    point = PuntosPago.objects.filter(pk=point_id, sucursalid_id=employee.sucursalid_id).first()
    if not point:
        raise ReplicaScopeError("La caja no pertenece a tu sucursal autorizada.")
    return {"device_id": str(settings.LOCAL_CONFIG["instance_id"]), "user_id": actor.pk,
        "branch_id": employee.sucursalid_id, "point_id": point.pk,
        "clients": user_can_access_url_name(actor, "cliente_autocomplete"), "routes": snapshot["routes"]}


def capture(user_id, point_id):
    cloud = connections["default"]
    if offline_request() or cloud.vendor != "postgresql" or cloud.in_atomic_block:
        raise ReplicaError("La copia necesita una conexión PostgreSQL independiente a la nube.")
    # This wrapper is thread-local. Assign a COPY, never mutate global DATABASES
    # or the settings used by concurrent sale requests.
    original_settings = cloud.settings_dict
    cloud.close()
    cloud.settings_dict = {**original_settings, "OPTIONS": {**original_settings.get("OPTIONS", {}),
        "connect_timeout": CLOUD_CONNECT_TIMEOUT, "options": "-c statement_timeout=15000 -c lock_timeout=3000"}}
    try:
        with transaction.atomic(using="default"):
            with cloud.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            try:
                identity = collect_identity(user_id)
            except Usuario.DoesNotExist:
                raise ReplicaScopeError("El usuario ya no está autorizado.") from None
            scope = authorize(identity, point_id)
            if getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
                from .integrated_sale_snapshot import capture as capture_sales
                capture_sales(identity, scope)
                from .integrated_business_snapshot import capture as capture_business
                capture_business(identity, scope)
            rows = collect(scope)
            validate_rows(rows, scope)
            return identity, scope, rows, timezone.now()
    finally:
        cloud.close()
        cloud.settings_dict = original_settings


def _source():
    return SOURCE_PREFIX + cloud_fingerprint()


def _assert_binding(state, user_id, point_id):
    if state and (state.server != _source() or state.local_user_id != user_id
            or state.device_id != UUID(str(settings.LOCAL_CONFIG["instance_id"]))
            or state.scope.get("point_id") != point_id):
        raise ReplicaError("Esta copia pertenece a otro usuario, nube o caja. No se reemplazó; se requiere revisar la vinculación.")


def _assert_clean(node):
    if (LocalCommand.objects.using("offline").filter(node=node).exclude(state="accepted").exists()
            or LocalSaleSession.objects.using("offline").filter(node=node).exists()):
        raise ReplicaError("Hay operaciones o una sesión offline por conciliar. Se conservó la copia anterior.")


def apply_snapshot(identity, scope, rows, captured_at):
    """Identity, permissions, catalog and metadata commit together or not at all."""
    now = timezone.now()
    if captured_at + timedelta(minutes=5) < now:
        raise ReplicaError("La descarga tardó demasiado; vuelve a intentarlo.")
    with transaction.atomic(using="offline"):
        node = LocalNode.objects.using("offline").select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        _assert_clean(node)
        state = ReplicaState.objects.using("offline").filter(node=node).first()
        _assert_binding(state, scope["user_id"], scope["point_id"])
        if not state:
            # remember_user may have created ONLY the actor's branch earlier.
            # Adopt that exact, current row, never arbitrary seeded/business data.
            existing = current_rows("offline")
            allowed = [row for row in rows if row["entity"] == "branch"]
            seeded_identity = IntegratedIdentity.objects.using("offline").filter(node=node,
                user_id=scope["user_id"], cloud_fingerprint=cloud_fingerprint()).exists()
            if existing and (existing != allowed or not seeded_identity):
                raise ReplicaError("La base local contiene datos ajenos a esta copia; no se sobrescribieron.")
            apply_identity(identity, now=captured_at)
            # apply_identity creates the branch to satisfy the employee FK.
            ReplicaRow.objects.using("offline").bulk_create([ReplicaRow(node=node, entity=row["entity"],
                source_id=row["id"], values=row["values"]) for row in current_rows("offline")])
            state = ReplicaState.objects.using("offline").create(node=node, local_user_id=scope["user_id"],
                server=_source(), device_id=node.pk, scope=scope)
        before = {row_key(row): row for row in [{"entity": r.entity, "id": r.source_id, "values": r.values}
            for r in ReplicaRow.objects.using("offline").filter(node=node)]}
        # Permission changes are allowed, but never a change of branch/owner.
        if state.scope.get("branch_id") != scope["branch_id"]:
            raise ReplicaScopeError("Cambió la sucursal del usuario. Se bloqueó la copia anterior.")
        after = {row_key(row): row for row in rows}
        changes = [{"op": "upsert", **row} for key, row in after.items()
            if not state.active_id or before.get(key) != row]
        changes += [{"op": "delete", "entity": row["entity"], "id": row["id"]}
            for key, row in before.items() if key not in after]
        transfer = ReplicaTransfer.objects.using("offline").create(id=uuid4(), node=node,
            session_id=uuid4(), base_id=state.active_id, downloaded=True, next_offset=len(changes))
        transfer.descriptor = {"protocol": VERSION, "snapshot_id": str(transfer.pk),
            "base_id": str(state.active_id) if state.active_id else None, "scope": scope,
            "manifest": manifest(rows), "counts": dict(Counter(row["entity"] for row in rows)),
            "changes_count": len(changes), "page_size": PAGE_SIZE, "sale_cursor": {},
            "expires_at": authorization_expiry(captured_at).isoformat()}
        transfer.save(using="offline", update_fields=["descriptor"])
        ReplicaPage.objects.using("offline").bulk_create([ReplicaPage(transfer=transfer, offset=i,
            changes=changes[i:i + PAGE_SIZE]) for i in range(0, len(changes), PAGE_SIZE)])
        state.scope = scope
        state.save(using="offline", update_fields=["scope"])
        state = activate(transfer.pk, using="offline")
        apply_identity(identity, copy_branch=False, now=captured_at)
        from .integrated_sale_snapshot import apply as apply_sales
        apply_sales(scope)
        from .integrated_business_snapshot import apply as apply_business
        apply_business(scope)
        return state


def synchronize(user_id, point_id):
    if not getattr(settings, "NOVA_REFERENCE_ENABLED", False):
        raise ReplicaError("La copia integrada no está habilitada en este equipo.")
    if type(user_id) is not int or type(point_id) is not int or user_id < 1 or point_id < 1:
        raise ReplicaError("Usuario o caja inválidos.")
    local = connections["offline"]
    if local.in_atomic_block:
        raise ReplicaError("La descarga debe comenzar fuera de una transacción local.")
    node_id = settings.LOCAL_CONFIG["instance_id"]
    lock_id = int.from_bytes(hashlib.sha256(("reference:" + str(node_id)).encode()).digest()[:8], "big", signed=True)
    with local.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        if not cursor.fetchone()[0]:
            raise ReplicaError("Ya hay una actualización en curso en este equipo.")
    try:
        state = ReplicaState.objects.using("offline").filter(node_id=node_id).first()
        _assert_binding(state, user_id, point_id)
        _assert_clean(node_id)
        identity, scope, rows, captured_at = capture(user_id, point_id)
        return apply_snapshot(identity, scope, rows, captured_at)
    except ReplicaScopeError:
        IntegratedIdentity.objects.using("offline").filter(node_id=node_id, user_id=user_id).update(expires_at=timezone.now())
        ReplicaState.objects.using("offline").filter(node_id=node_id, local_user_id=user_id).update(
            blocked=True, status="Autorización retirada; copia conservada sin acceso offline")
        raise
    except Exception:
        ReplicaState.objects.using("offline").filter(node_id=node_id, local_user_id=user_id, server=_source()).update(
            status="Actualización interrumpida; copia anterior conservada")
        raise
    finally:
        with local.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
