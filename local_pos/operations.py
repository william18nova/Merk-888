"""Solicitudes offline: devolución/cierre NO se completan sin acuse de nube."""
from decimal import Decimal, ROUND_HALF_UP
from django.db import transaction
from mainApp.permissions import user_can_change_sale, user_can_access_url_name
from mainApp.services.hybrid_operations import normalize_return, normalize_close
from pos_shared.protocol import ProtocolError, amount
from .models import LocalNode, LocalSaleSession
from . import sales
from .business import submit


def sale_snapshot(actor, sale_id, remote, *, using="default"):
    if not user_can_change_sale(actor):
        raise ProtocolError("No tienes permiso para devolver productos.")
    state = sales.scope(actor, using=using)
    key = str(int(sale_id))
    try:
        result = remote.call("operation-read", {"action": "sale", "sale_id": int(key)})
    except Exception as exc:
        from hybrid_client.client import RemoteError
        if not isinstance(exc, RemoteError) or exc.status not in sales.TRANSIENT:
            raise
        session = LocalSaleSession.objects.using(using).get(node_id=state.node_id)
        result = session.data.get("return_snapshots", {}).get(key)
        if result is None:
            raise ProtocolError("Necesitas conexión para consultar esta venta por primera vez.") from None
        return result
    if (result.get("sale", {}).get("sale_id") != int(key)
            or result.get("sale", {}).get("session_id") != remote.session_id
            or not isinstance(result.get("authorization"), str)):
        raise ProtocolError("El servidor no autorizó la consulta de esa venta.")
    with transaction.atomic(using=using):
        LocalNode.objects.using(using).select_for_update().get(pk=state.node_id)
        session = LocalSaleSession.objects.using(using).get(node_id=state.node_id)
        cached = session.data.get("return_snapshots", {})
        cached.pop(key, None)
        cached[key] = result
        cached = dict(list(cached.items())[-30:])
        session.data = {**session.data, "return_snapshots": cached}
        session.save(using=using, update_fields=["data"])
    return result


def return_total(snapshot, items):
    rows = {row["detail_id"]: row for row in snapshot["sale"]["items"]}
    total = amount(snapshot["sale"]["total"])
    gross = sum((Decimal(row["quantity"])*amount(row["unit_price"]) for row in rows.values()), Decimal("0"))
    partial = Decimal("0")
    for item in items:
        row = rows.get(item["detail_id"])
        if not row or type(item["quantity"]) is not int or not 1 <= item["quantity"] <= row["quantity"]:
            raise ProtocolError("Cantidad fuera de lo disponible en la consulta de la venta.")
        partial += Decimal(item["quantity"])*amount(row["unit_price"])
    partial = partial.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if gross > 0 and total < gross:
        partial = (partial*total/gross).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return min(total, partial)


def prepare_return(state, session, data, using="default"):
    if set(data) != {"operation_id", "session_id", "data"} or not session.data.get("operation_permissions", {}).get("return"):
        raise ProtocolError("No existe autorización local para esta devolución.")
    normalized = normalize_return(data["data"])
    snapshot = session.data.get("return_snapshots", {}).get(str(normalized["sale_id"]))
    if not snapshot or normalized["authorization"] != snapshot["authorization"]:
        raise ProtocolError("Consulta primero la venta en este equipo.")
    total = return_total(snapshot, normalized["items"])
    if total != amount(normalized["expected_total"]) or sum((amount(value) for value in normalized["refunds"].values()), Decimal("0")) != total:
        raise ProtocolError("Revisa el importe y los medios del reintegro.")
    allowed = {row["code"] for row in session.data.get("payment_methods", [])}
    if not set(normalized["refunds"]).issubset(allowed):
        raise ProtocolError("Medio de reintegro no autorizado.")
    rows = {row["detail_id"]: row for row in snapshot["sale"]["items"]}
    delta = {}
    for item in normalized["items"]:
        pid = str(rows[item["detail_id"]]["product_id"])
        delta[pid] = delta.get(pid, 0)+item["quantity"]
    return {"kind": "return.v1", "data": normalized}, {
        "inventory_delta": {}, "expected_delta": delta, "projected": False, "total": str(total),
        "summary": f"Solicitud de devolución de venta #{normalized['sale_id']}",
        "message": "Pendiente de conciliación. No entregues el reintegro hasta la confirmación.", "receipt": ""}


def create_return(actor, data, remote, *, using="default"):
    if not user_can_change_sale(actor):
        raise ProtocolError("No tienes permiso para devolver productos.")
    return submit(actor, "return.v1", data, remote, prepare_return, using=using)


def prepare_close(state, session, data, using="default"):
    if set(data) != {"operation_id", "session_id", "data"} or not session.data.get("operation_permissions", {}).get("turn.close"):
        raise ProtocolError("No tienes autorización local para cerrar caja.")
    normalized = normalize_close(data["data"])
    if normalized["turn_id"] != session.data.get("turn_id"):
        raise ProtocolError("El turno no corresponde a la sesión del equipo.")
    allowed = {row["code"] for row in session.data.get("payment_methods", []) if not row.get("is_cash")}
    if not set(normalized["methods"]).issubset(allowed):
        raise ProtocolError("Conteo de un medio no autorizado.")
    return {"kind": "turn.close.v1", "data": normalized}, {
        "inventory_delta": {}, "projected": True, "turn_id": normalized["turn_id"],
        "summary": "Declaración de cierre", "message": "Pendiente de sincronizar y conciliar; el turno no está cerrado todavía.", "receipt": ""}


def create_close(actor, data, remote, *, using="default"):
    if not user_can_access_url_name(actor, "turno_caja_cerrar"):
        raise ProtocolError("No tienes permiso para cerrar caja.")
    return submit(actor, "turn.close.v1", data, remote, prepare_close, using=using)
