"""Consulta y reintentos auditados. Nunca cambia importes, UUID ni secuencias."""
from uuid import UUID

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from mainApp.permissions import is_web_master_role, user_has_permission
from pos_shared.protocol import ProtocolError
from .models import LocalCommand, LocalNode, LocalSaleSession, ReplicaState
from . import sales

KINDS = {sales.KIND: "Venta", "expense.create.v1": "Pago / egreso",
         "return.v1": "Devolución", "turn.close.v1": "Cierre de caja"}
STATES = {"intent": "Preparada", "pending": "Pendiente", "conflict": "Por revisar", "accepted": "Confirmada"}
# Solo códigos conocidos. No guardar respuestas remotas, URLs ni credenciales.
REASONS = {
    "unavailable": ("No hubo comunicación con el servidor.", "Comprueba la conexión. El reintento usa la misma referencia."),
    "server": ("El servidor no pudo completar la solicitud.", "Espera un momento y vuelve a sincronizar."),
    "ack_invalid": ("La respuesta no confirmó la operación de forma válida.", "No repitas el cobro: volveremos a consultar con la misma referencia."),
    "authentication": ("La vinculación del equipo ya no fue reconocida.", "El Web Master debe revisar el acceso del equipo; conserva esta instalación."),
    "permission": ("El usuario no tiene el permiso requerido en la nube.", "Revisa sus permisos antes de verificar otra vez. El autor original no se cambia."),
    "revoked": ("El equipo está desactivado en la nube.", "Solicita al Web Master que revise la autorización del equipo."),
    "disabled": ("El modo híbrido está desactivado en el servidor.", "El Web Master debe revisar la configuración del piloto."),
    "recovery": ("Este equipo tiene una recuperación pendiente.", "No uses otra copia de la misma instalación. Solicita revisión al Web Master."),
    "session": ("La sesión no corresponde a la autorización del equipo.", "Conserva los pendientes y solicita conciliación. No abras otra identidad."),
    "session_expired": ("La autorización de la sesión venció.", "Conserva los pendientes y solicita revisión de la sesión."),
    "turn_closed": ("El turno ya está cerrado en la nube.", "Un responsable debe conciliar el turno. No registres de nuevo la venta."),
    "turn": ("El turno no está disponible para esta operación.", "Revisa su estado en la nube antes de volver a verificar."),
    "sequence": ("El orden de las operaciones no coincide con la nube.", "No saltes pendientes. Solicita conciliación de las referencias y la sesión."),
    "idempotency": ("La referencia ya está asociada a datos distintos.", "Requiere conciliación del Web Master. No cambies la referencia ni repitas el cobro."),
    "clock": ("La fecha quedó fuera de la autorización del equipo.", "Revisa el reloj y solicita conciliación; no cambies la fecha del movimiento guardado."),
    "quote": ("El servidor no reconoció una autorización de precio o de pago.", "Conserva el movimiento original para revisión; no reemplaces sus importes."),
    "product": ("Un producto fue retirado o cambió de tipo.", "Revisa el producto en la nube antes de verificar de nuevo."),
    "method": ("El medio de pago fue retirado o dejó de estar autorizado.", "Revisa el medio de pago en la nube; no cambies cómo se recibió el dinero."),
    "return_conflict": ("Cambió la cantidad disponible para devolver.", "Revisa las devoluciones de esa venta. No entregues dinero sin confirmación."),
    "return_total": ("Cambió el valor disponible para el reintegro.", "Requiere conciliación de la devolución. No entregues dinero sin confirmación."),
    "pending": ("Otra operación o autorización impide completar el cierre.", "Revisa los pendientes de los equipos antes de verificar el cierre."),
    "close": ("El cierre no pasó la conciliación del servidor.", "Un responsable debe revisar el turno y sus movimientos."),
    "scope": ("La operación está fuera del punto o sucursal autorizados.", "Solicita revisión de la vinculación. No muevas la operación a otro equipo."),
    "blocked_by_previous": ("Una operación anterior está deteniendo la cola.", "Resuelve primero la operación anterior. La secuencia no se puede saltar."),
    "unknown": ("La operación necesita revisión, pero no hay una causa detallada guardada.", "Un responsable puede verificarla de nuevo sin cambiar sus datos."),
}


def manager(actor):
    return bool(actor.is_authenticated and actor.is_active and
                (is_web_master_role(actor) or user_has_permission(actor, "caja_turnos_editar")))


def visible_commands(actor, *, using="default"):
    if not getattr(settings, "HYBRID_LOCAL_ENABLED", False) or not actor.is_authenticated or not actor.is_active:
        raise PermissionDenied
    rows = LocalCommand.objects.using(using).filter(node_id=settings.LOCAL_CONFIG["instance_id"])
    return rows if manager(actor) else rows.filter(actor_id=actor.pk)


def connection_context(actor, *, using="default"):
    visible_commands(actor, using=using)  # Comprueba usuario activo y modo local.
    state = ReplicaState.objects.using(using).filter(node_id=settings.LOCAL_CONFIG["instance_id"]).first()
    if not state or not (manager(actor) or state.local_user_id == actor.pk):
        return None, None
    return state, LocalSaleSession.objects.using(using).filter(node_id=state.node_id).first()


def error_metadata(exc, previous):
    code = exc.code if isinstance(exc.code, str) and exc.code in REASONS else "unknown"
    return {**previous, "code": code, "http_status": exc.status if type(exc.status) is int else None,
            "last_attempt": timezone.now().isoformat(), "attempts": int(previous.get("attempts", 0)) + 1}


def record_attempt(command, error=None):
    previous = command.local_result.get("sync", {})
    metadata = error_metadata(error, previous) if error else {
        "code": "", "http_status": 200, "last_attempt": timezone.now().isoformat(),
        "attempts": int(previous.get("attempts", 0)) + 1}
    history = list(command.local_result.get("review_history", []))
    if history and "outcome" not in history[-1]:
        history[-1] = {**history[-1], "outcome": command.state, "completed_at": metadata["last_attempt"]}
    command.local_result = {**command.local_result, "sync": metadata, "review_history": history}


def retry_conflict(actor, operation_id, *, request_id, note, remote_factory, using="default"):
    """Un responsable libera SOLO la cabeza de cola; acuse remoto sigue obligatorio."""
    if not manager(actor):
        raise PermissionDenied
    sales.enabled()  # No liberar una revisión si el adaptador está deshabilitado.
    token = str(UUID(str(request_id)))
    note = str(note).strip()
    if not 8 <= len(note) <= 400:
        raise ProtocolError("Explica qué revisaste, entre 8 y 400 caracteres.")
    with transaction.atomic(using=using):
        node = LocalNode.objects.using(using).select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        command = visible_commands(actor, using=using).get(pk=operation_id)
        history = command.local_result.get("review_history", [])
        if any(event.get("request_id") == token for event in history):
            return command.state  # Doble clic / reenvío: no genera otro reintento.
        first = LocalCommand.objects.using(using).filter(node=node).exclude(state="accepted").order_by("sequence").first()
        state, session = connection_context(actor, using=using)
        if (not state or not session or not first or first.pk != command.pk
                or command.kind not in sales.KINDS or command.state != "conflict"
                or command.payload.get("session_id") != str(session.session_id)):
            raise ProtocolError("Esta operación ya cambió o hay otra anterior. Actualiza la pantalla y revisa la primera pendiente.")
        if len(history) >= 100:
            raise ProtocolError("Esta operación acumula demasiados intentos. Conserva el historial y solicita conciliación técnica.")
        transport = remote_factory()
        if (transport.session_id != str(session.session_id) or transport.url != state.server
                or transport.device_id != str(state.device_id)):
            raise ProtocolError("La vinculación no coincide. No se cambió el pendiente.")
        event = {"request_id": token, "at": timezone.now().isoformat(), "actor_id": actor.pk,
                 "actor": actor.get_username(), "action": "retry_requested", "note": note,
                 "previous_code": command.local_result.get("sync", {}).get("code", "unknown")}
        command.local_result = {**command.local_result, "review_history": [*history, event]}
        command.state = "pending"
        command.save(using=using, update_fields=["state", "local_result"])
    # El historial queda durable ANTES del envío. Si se interrumpe, el worker
    # retoma la misma referencia. Nunca se elimina ni se fuerza una aceptación.
    sales.flush(transport, using=using, limit=1)
    return LocalCommand.objects.using(using).get(pk=operation_id).state


def synchronize_pending(actor, remote_factory, *, using="default"):
    state, session = connection_context(actor, using=using)
    if not state or not session or not getattr(settings, "HYBRID_LOCAL_SALES_ENABLED", False):
        raise ProtocolError("Primero vincula este equipo y prepara una sesión autorizada.")
    if not manager(actor) and state.local_user_id != actor.pk:
        raise PermissionDenied
    sales.flush(remote_factory(), using=using, limit=5)
    # No descargar catálogos ni renovar autorizaciones en un POST de revisión.
    # El trabajador del runtime continúa ese proceso en segundo plano.
