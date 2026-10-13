"""Local shift identities are mapped through the opening's cloud receipt."""
from django.db.models import Q
from mainApp.models import TurnoCaja, Usuario, PuntosPago, SolicitudPOS
from .integrated_offline_sales import SaleError


def build_open(request, state):
    from .integrated_business import money
    scope = state.scope
    if not scope.get("offline_sale", {}).get("required_turn"):
        raise SaleError("El inicio manual de turnos está desactivado.")
    actor = request.POST.get("usuario_id") or request.POST.get("cajero_id")
    if str(actor) != str(request.user.pk) or str(request.POST.get("puntopago_id")) != str(scope["point_id"]):
        raise SaleError("Offline solo puedes abrir tu turno en la caja asignada a este equipo.")
    if TurnoCaja.objects.filter(Q(cajero_id=request.user.pk) | Q(puntopago_id=scope["point_id"]),
            estado__in=["ABIERTO", "CIERRE"]).exists():
        raise SaleError("Ya hay un turno abierto o en cierre. Retómalo desde Turno de caja antes de abrir otro.")
    # Positive IDs also work with Django's <int:turno_id> URLs. Never sent as
    # cloud IDs: all later events carry the opening UUID instead.
    local_id = 1500000000 + state.node.sequence + 1
    while TurnoCaja.objects.filter(pk=local_id).exists():
        local_id += 1
    return {"turn_id": local_id, "base": str(money(request.POST.get("saldo_apertura_efectivo") or 0))}


def project_open(values, state, occurred):
    turn = TurnoCaja.objects.create(pk=values["turn_id"], cajero_id=state.local_user_id,
        puntopago_id=state.scope["point_id"], saldo_apertura_efectivo=values["base"], estado="ABIERTO")
    TurnoCaja.objects.filter(pk=turn.pk).update(inicio=occurred)
    state.scope = {**state.scope,
        "offline_sale": {**state.scope["offline_sale"], "turn_id": turn.pk,
            "turn_started": occurred.isoformat(), "turn_operation": values["operation_id"]},
        "offline_business": {**state.scope["offline_business"], "turn": {"id": turn.pk}, "has_ptm": False}}


def resolve_turn(payload):
    reference = payload.get("turn_operation")
    if not reference:
        return payload.get("turn_id")
    receipt = SolicitudPOS.objects.filter(pk=reference, usuario_id=payload["actor_id"], accion="open").first()
    data = receipt.respuesta if receipt else {}
    if not data.get("success") or data.get("node_id") != payload["node_id"] or data.get("point_id") != payload["point_id"]:
        raise SaleError("Falta confirmar la apertura de este turno. No se enviaron sus movimientos a otro turno.")
    return data["turno_id"]


def upload_open(command, actor, occurred):
    from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE, locked_feature_enabled
    payload, values = command.payload, command.payload["values"]
    if not locked_feature_enabled(TURN_REQUIRED_FEATURE):
        raise SaleError("Cambió la configuración de turnos; revisa la apertura pendiente.")
    # Lock in the same point-first order as the existing online opening.
    point = PuntosPago.objects.select_for_update().get(pk=payload["point_id"], sucursalid_id=payload["branch_id"])
    Usuario.objects.select_for_update().get(pk=actor.pk)
    if TurnoCaja.objects.filter(Q(puntopago=point) | Q(cajero=actor)).filter(
            Q(estado__in=["ABIERTO", "CIERRE"]) | Q(fin__gt=occurred) | Q(inicio__gte=occurred)).exists():
        raise SaleError("Otro turno ocupa ese horario o esa caja. La apertura offline se conservó para conciliación.")
    turn = TurnoCaja.objects.create(puntopago=point, cajero=actor, estado="ABIERTO", saldo_apertura_efectivo=values["base"])
    TurnoCaja.objects.filter(pk=turn.pk).update(inicio=occurred)
    return {"success": True, "turno_id": turn.pk, "local_turn_id": values["turn_id"],
        "node_id": payload["node_id"], "point_id": point.pk}
