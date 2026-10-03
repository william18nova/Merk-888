"""Un diario y una secuencia para todos los movimientos de una sesión local."""
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from pos_shared.protocol import PROTOCOL, ProtocolError, fingerprint
from .models import LocalNode, LocalCommand, LocalSaleSession

KINDS = {"expense.create.v1", "return.v1", "turn.close.v1"}


def submit(actor, kind, data, remote, prepare, *, using="default"):
    from .sales import scope, flush, public_operation
    if not getattr(settings, "HYBRID_LOCAL_OPERATIONS_ENABLED", False) or kind not in KINDS:
        raise ProtocolError("Esta operación híbrida no está habilitada.")
    state = scope(actor, using=using)
    if not isinstance(data, dict):
        raise ProtocolError("Datos inválidos.")
    operation_id = UUID(str(data.get("operation_id")))
    identity = fingerprint({"actor": actor.pk, "kind": kind, "request": data})
    with transaction.atomic(using=using):
        node = LocalNode.objects.using(using).select_for_update().get(pk=state.node_id)
        state = scope(actor, using=using)
        session = LocalSaleSession.objects.using(using).get(node=node)
        if str(session.session_id) != data.get("session_id"):
            raise ProtocolError("La página pertenece a otra sesión.")
        old = LocalCommand.objects.using(using).filter(pk=operation_id).first()
        if old:
            if old.node_id != node.pk or old.fingerprint != identity:
                raise ProtocolError("La referencia pertenece a otra operación; no se duplicó.")
        else:
            if session.data.get("closing") or session.data.get("closed"):
                raise ProtocolError("Ya declaraste el cierre; no se admiten más movimientos en esta sesión.")
            if kind != "turn.close.v1" and session.data.get("close_draft", {}).get("step", 1) > 1:
                raise ProtocolError("El conteo de cierre está en curso; no registres nuevos movimientos.")
            if LocalCommand.objects.using(using).filter(node=node, state="conflict").exists():
                raise ProtocolError("Hay una operación por revisar. Conserva los pendientes.")
            now = timezone.now()
            expiry = parse_datetime(session.data["expires_at"])
            if now >= expiry or (session.last_clock and now < session.last_clock-timedelta(minutes=2)):
                raise ProtocolError("La autorización venció o el reloj retrocedió.")
            fields, result = prepare(state, session, data, using=using)
            session.sequence += 1
            session.last_clock = max(now, session.last_clock or now)
            if kind == "turn.close.v1":
                session.data = {**session.data, "closing": True}
            session.save(using=using, update_fields=["sequence", "last_clock", "data"])
            node.sequence += 1
            node.save(using=using, update_fields=["sequence"])
            payload = {**fields, "protocol": PROTOCOL, "operation_id": str(operation_id),
                       "session_id": str(session.session_id), "sequence": session.sequence,
                       "occurred_at": now.isoformat()}
            LocalCommand.objects.using(using).create(operation_id=operation_id, node=node,
                sequence=node.sequence, actor=actor, kind=kind, fingerprint=identity,
                payload=payload, local_result={**result, "occurred_at": now.isoformat()}, state="intent")
    flush(remote, using=using, limit=1)
    with transaction.atomic(using=using):
        LocalNode.objects.using(using).select_for_update().get(pk=state.node_id)
        row = LocalCommand.objects.using(using).get(pk=operation_id)
        if row.state == "intent":
            row.state = "pending"
            row.save(using=using, update_fields=["state"])
        return public_operation(row)
