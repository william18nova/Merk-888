"""Durable intent and acknowledgement for ORIGINAL online sales.

No stored form, credentials, discount code or arbitrary SQL replay. A timeout
remains uncertain until a locked cloud receipt check succeeds. This stage never
converts an uncertain online request into an offline sale or resubmits it.
"""
from contextlib import contextmanager
import hashlib
import json
import logging
from uuid import UUID

from django.conf import settings
from django.db import connections, transaction, DatabaseError
from django.http import JsonResponse
from django.utils import timezone

from mainApp.models import Usuario
from mainApp.services.pos_request_receipts import HEADER, failure, request_fingerprint, recover_receipt
from .integrated_auth import cloud_fingerprint
from .models import LocalCommand, LocalNode, ReplicaState
from .request_context import offline_request

logger = logging.getLogger(__name__)
KIND = "integrated.sale.request.v1"
TERMINAL_RETRY = {"rejected", "absent"}


class JournalError(ValueError):
    pass


@contextmanager
def operation_lock(operation_id):
    """Cross-process lock held across network I/O, but NOT a local transaction."""
    local = connections["offline"]
    if local.in_atomic_block:
        raise JournalError("El registro debe comenzar fuera de una transacción local.")
    key = int.from_bytes(hashlib.sha256(b"nova-original-journal:" + operation_id.bytes).digest()[:8], "big", signed=True)
    with local.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [key])
        locked = cursor.fetchone()[0]
    try:
        yield locked
    finally:
        if locked:
            try:
                with local.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [key])
            except DatabaseError:
                # Do not turn a committed sale into a failed response because
                # the local connection died while releasing its session lock.
                local.close()


def prepare_intent(actor, operation_id, digest):
    if not actor.is_authenticated or not actor.is_active:
        raise JournalError("Inicia sesión para registrar la venta.")
    with transaction.atomic(using="offline"):
        node = LocalNode.objects.using("offline").select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        state = ReplicaState.objects.using("offline").filter(node=node).first()
        source = cloud_fingerprint()
        if (not state or not state.active_id or state.local_user_id != actor.pk
                or state.server != "django-db:" + source or state.device_id != node.pk):
            raise JournalError("Primero prepara la copia autorizada para este usuario y equipo.")
        command = LocalCommand.objects.using("offline").filter(pk=operation_id).first()
        if command:
            if (command.node_id != node.pk or command.actor_id != actor.pk or command.kind != KIND
                    or command.payload != {"source": source}):
                raise JournalError("Esta referencia pertenece a otra venta, usuario o nube.")
            if command.state == "conflict":
                raise JournalError("Esta referencia necesita revisión; no repitas el cobro.")
            retryable = command.state == "accepted" and command.local_result.get("outcome") in TERMINAL_RETRY
            if command.fingerprint != digest and not retryable:
                raise JournalError("La venta anterior no está resuelta. Conserva su carrito sin modificarlo ni repetir el cobro.")
            if command.state == "accepted" and command.local_result.get("outcome") == "confirmed":
                return command
            command.fingerprint = digest
            command.state = "intent"
            command.local_result = {"outcome": "sending", "attempt": command.local_result.get("attempt", 0) + 1,
                "attempt_started_at": timezone.now().isoformat()}
            command.save(using="offline", update_fields=["fingerprint", "state", "local_result"])
        else:
            node.sequence += 1
            node.save(using="offline", update_fields=["sequence"])
            command = LocalCommand.objects.using("offline").create(operation_id=operation_id,
                node=node, sequence=node.sequence, actor_id=actor.pk, kind=KIND, payload={"source": source},
                fingerprint=digest, state="intent", local_result={"outcome": "sending", "attempt": 1,
                    "attempt_started_at": timezone.now().isoformat()})
        return command


def record_result(command, outcome, response=None):
    result = {**command.local_result, "outcome": outcome}
    if outcome == "confirmed":
        if (not isinstance(response, dict) or response.get("success") is not True
                or type(response.get("venta_id")) is not int or response["venta_id"] < 1):
            raise JournalError("La confirmación recibida no identifica una venta válida.")
        result["response"] = {key: value for key, value in response.items() if key != "print_token"}
    state = "accepted" if outcome in {"confirmed", "rejected", "absent"} else "conflict" if outcome == "conflict" else "pending"
    LocalCommand.objects.using("offline").filter(pk=command.pk, fingerprint=command.fingerprint).update(state=state, local_result=result)


def confirmed_response(command):
    response = JsonResponse(command.local_result["response"])
    response[HEADER] = str(command.pk)
    response["X-Nova-Replayed"] = "1"
    response["Cache-Control"] = "no-store"
    return response


def protect_original_request(request, execute):
    if offline_request():
        from .integrated_offline_sales import save
        return save(request)
    try:
        operation_id = UUID(request.headers.get(HEADER, ""))
        if operation_id.version != 4:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        return failure("Actualiza la página; falta una referencia válida para proteger la venta.", "operation_required", 409)
    try:
        with operation_lock(operation_id) as locked:
            if not locked:
                return failure("Esta venta ya se está verificando. Espera unos segundos; no repitas el cobro.", "operation_busy", 409)
            from .integrated_offline_sales import KIND as OFFLINE_KIND, replay, SaleError
            previous = LocalCommand.objects.using("offline").filter(pk=operation_id, kind=OFFLINE_KIND).first()
            if previous:
                try:
                    return replay(request, previous)
                except SaleError as exc:
                    raise JournalError(str(exc)) from None
            command = prepare_intent(request.user, operation_id, request_fingerprint(request))
            if command.state == "accepted":
                return confirmed_response(command)
            # prepare_intent has COMMITTED before the first cloud write.
            try:
                response = execute()
            except Exception:
                try:
                    record_result(command, "uncertain")
                except DatabaseError:
                    pass  # The already committed intent remains recoverable.
                logger.warning("POS: venta sin confirmación; se conserva la referencia.")
                return failure("No se pudo confirmar la respuesta de Aiven. Conserva el carrito y no repitas el cobro; se verificará automáticamente.", "sale_uncertain", 503)
            try:
                data = json.loads(response.content)
                outcome = ("confirmed" if response.status_code == 200 and data.get("success") is True
                    else "conflict" if data.get("code") == "operation_conflict" else "rejected")
                # The protected cloud transaction has ended. A normal validation
                # rejection rolled it back; no passwords/error SQL are persisted.
                record_result(command, outcome, data)
            except (DatabaseError, ValueError, TypeError, AttributeError):
                logger.warning("POS: confirmación local pendiente; se conserva la respuesta original de la nube.")
            return response
    except JournalError as exc:
        return failure(str(exc), "journal_conflict", 409)
    except DatabaseError:
        return failure("No se pudo proteger la venta en este equipo. No se envió un nuevo cobro; conserva el carrito.", "journal_unavailable", 503)


def read_cloud_confirmation(command):
    if command.payload != {"source": cloud_fingerprint()}:
        raise PermissionError("La operación pertenece a otra nube.")
    cloud = connections["default"]
    if offline_request() or cloud.in_atomic_block:
        raise JournalError("La confirmación necesita una conexión independiente a la nube.")
    original = cloud.settings_dict
    cloud.close()
    cloud.settings_dict = {**original, "OPTIONS": {**original.get("OPTIONS", {}),
        "connect_timeout": 6, "options": "-c statement_timeout=5000 -c lock_timeout=1500"}}
    try:
        with transaction.atomic():
            with cloud.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            actor = Usuario.objects.get(pk=command.actor_id)
            return recover_receipt(actor, command.pk, expected_fingerprint=command.fingerprint)
    finally:
        cloud.close()
        cloud.settings_dict = original


def reconcile_pending(limit=20):
    """Read confirmations only; never replay forms or recreate business writes."""
    commands = list(LocalCommand.objects.using("offline").filter(node_id=settings.LOCAL_CONFIG["instance_id"],
        kind=KIND, state__in=["intent", "pending"]).order_by("sequence").values_list("pk", flat=True)[:limit])
    resolved = 0
    for operation_id in commands:
        with operation_lock(operation_id) as locked:
            if not locked:
                continue
            command = LocalCommand.objects.using("offline").get(pk=operation_id)
            if command.state not in {"intent", "pending"}:
                continue
            try:
                result = read_cloud_confirmation(command)
            except (PermissionError, Usuario.DoesNotExist):
                record_result(command, "conflict")
                continue
            # Any other exception preserves uncertainty; NEVER means absent.
            if result.get("status") == "accepted":
                record_result(command, "confirmed", result["response"])
            elif result.get("status") == "absent":
                record_result(command, "absent")
            else:
                raise JournalError("Respuesta de confirmación inválida.")
            resolved += 1
    return resolved
