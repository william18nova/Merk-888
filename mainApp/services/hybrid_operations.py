"""Devoluciones y cierre conciliados: ninguna petición local ejecuta SQL libre."""
import json
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.http import HttpRequest, QueryDict
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from mainApp.models import (CambioDevolucion, DetalleVenta, OperacionHibrida,
                            SesionHibrida, TurnoCaja, Venta)
from mainApp.permissions import user_can_change_sale, user_can_access_url_name
from pos_shared.protocol import PROTOCOL, ProtocolError, amount, fingerprint
from . import hybrid

RETURN = "return.v1"
CLOSE = "turn.close.v1"
RETURN_SALT = "nova.hybrid.return-snapshot.v1"


def _integer(value, label, maximum=1000000000):
    if type(value) is not int or not 1 <= value <= maximum:
        raise hybrid.HybridError(f"{label} inválido.", "invalid", 400)
    return value


def normalize_return(data):
    if not isinstance(data, dict) or set(data) - {"sale_id", "items", "refunds", "expected_total", "authorization"}:
        raise ProtocolError("Datos de devolución no admitidos.")
    sale_id = _integer(data.get("sale_id"), "Venta")
    items, seen = data.get("items"), set()
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ProtocolError("Selecciona entre 1 y 100 detalles de la venta.")
    for item in items:
        if not isinstance(item, dict) or set(item) != {"detail_id", "quantity"}:
            raise ProtocolError("Detalle de devolución inválido.")
        key = _integer(item["detail_id"], "Detalle")
        _integer(item["quantity"], "Cantidad", 1000000)
        if key in seen:
            raise ProtocolError("No repitas un detalle en la devolución.")
        seen.add(key)
    refunds = data.get("refunds")
    if not isinstance(refunds, dict) or len(refunds) > 30:
        raise ProtocolError("Distribución del reintegro inválida.")
    for method, value in refunds.items():
        if not isinstance(method, str) or not 1 <= len(method) <= 50:
            raise ProtocolError("Medio de reintegro inválido.")
        amount(value)
    token = data.get("authorization")
    if not isinstance(token, str) or not 1 <= len(token) <= 30000:
        raise ProtocolError("Primero consulta la venta para autorizar su devolución.")
    return {"sale_id": sale_id, "items": items,
            "refunds": {key: str(amount(value)) for key, value in refunds.items()},
            "expected_total": str(amount(data.get("expected_total"))), "authorization": token}


def normalize_close(data):
    if not isinstance(data, dict) or set(data) - {"turn_id", "cash_counted", "bills_paid", "methods", "ptm_count"}:
        raise ProtocolError("Datos de cierre no admitidos.")
    methods = data.get("methods", {})
    if not isinstance(methods, dict) or len(methods) > 30:
        raise ProtocolError("Conteo por medios inválido.")
    normalized = {}
    for method, value in methods.items():
        if not isinstance(method, str) or not method or len(method) > 50 or method in {"efectivo", "facturas_pagadas"}:
            raise ProtocolError("El efectivo y las facturas tienen sus propios campos.")
        normalized[method] = str(amount(value))
    count = data.get("ptm_count", 0)
    if type(count) is not int or not 0 <= count <= 1000000:
        raise ProtocolError("El conteo PTM debe ser un entero no negativo.")
    return {"turn_id": _integer(data.get("turn_id"), "Turno"),
            "cash_counted": str(amount(data.get("cash_counted"))),
            "bills_paid": str(amount(data.get("bills_paid", "0"))),
            "methods": normalized, "ptm_count": count}


def _permission(session, kind):
    if kind == RETURN and not user_can_change_sale(session.usuario):
        raise hybrid.HybridError("No tienes permiso para hacer devoluciones.", "permission", 403)
    if kind == CLOSE and not user_can_access_url_name(session.usuario, "turno_caja_cerrar"):
        raise hybrid.HybridError("No tienes permiso para cerrar caja.", "permission", 403)


@transaction.atomic
def operation_read(device, data):
    device = hybrid.locked_device(device)
    hybrid.require_device(device)
    session = hybrid.load_session(device, data.get("session_id"))
    hybrid.require_user(session.usuario, device)
    from .payment_methods import payment_method_options
    methods = payment_method_options(active_only=True)
    if data.get("action") == "sale":
        _permission(session, RETURN)
        sale_id = _integer(data.get("sale_id"), "Venta")
        sale = Venta.objects.select_for_update().filter(pk=sale_id, puntopagoid_id=device.punto_id).first()
        if not sale:
            raise hybrid.HybridError("La venta no pertenece al punto de pago de este equipo.", "scope", 404)
        details = list(DetalleVenta.objects.filter(ventaid=sale, cantidad__gt=0).select_related("productoid").order_by("pk"))
        rows = [{"detail_id": row.pk, "product_id": row.productoid_id, "name": row.productoid.nombre,
                 "quantity": row.cantidad, "unit_price": str(row.preciounitario)} for row in details]
        authorized = {"session_id": str(session.pk), "sale_id": sale.pk, "total": str(sale.total), "items": rows}
        return {"sale": authorized, "authorization": signing.dumps(authorized, salt=RETURN_SALT, compress=True), "methods": methods}
    if data.get("action") == "turn":
        _permission(session, CLOSE)
        turn = TurnoCaja.objects.filter(pk=session.turno_id, cajero=session.usuario, puntopago=device.punto).first()
        if not turn:
            raise hybrid.HybridError("Esta sesión no tiene un turno propio para cerrar.", "turn", 409)
        return {"turn": {"id": turn.pk, "state": turn.estado, "base": str(turn.saldo_apertura_efectivo),
                         "point": device.punto.nombre}, "methods": methods}
    raise hybrid.HybridError("Consulta no admitida.", "action", 400)


def _return(session, device, data):
    _permission(session, RETURN)
    data = normalize_return(data)
    try:
        quote = signing.loads(data["authorization"], salt=RETURN_SALT)
        if quote["session_id"] != str(session.pk) or quote["sale_id"] != data["sale_id"]:
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise hybrid.HybridError("La consulta de la venta no corresponde a esta sesión.", "quote", 409) from None
    # Todos los equipos híbridos bloquean la MISMA venta antes de comprobar
    # cantidades. Dos solicitudes offline no pueden reintegrar dos veces.
    sale = Venta.objects.select_for_update().filter(pk=data["sale_id"], puntopagoid_id=device.punto_id).first()
    if sale is None:
        raise hybrid.HybridError("Venta fuera del punto autorizado.", "scope", 403)
    details = {row.pk: row for row in DetalleVenta.objects.select_for_update().filter(ventaid=sale)}
    authorized_ids = {row["detail_id"] for row in quote["items"]}
    rows, delta = [], {}
    for item in data["items"]:
        detail = details.get(item["detail_id"])
        if detail is None or detail.pk not in authorized_ids or detail.cantidad < item["quantity"]:
            raise hybrid.HybridError("La cantidad disponible para devolver cambió. Revisa la solicitud; no se hizo el reintegro.", "return_conflict")
        rows.append({"detalle": detail, "cantidad": item["quantity"]})
        delta[str(detail.productoid_id)] = delta.get(str(detail.productoid_id), 0) + item["quantity"]
    total = CambioDevolucion.calcular_total_devolucion(sale, rows)
    if total != amount(data["expected_total"]):
        raise hybrid.HybridError("El valor reintegrable cambió. Revisa antes de devolver dinero.", "return_total")
    try:
        CambioDevolucion.registrar_devolucion(sale, rows, reintegro_map=data["refunds"],
                                            registrado_por=session.usuario, turno_requerido=True)
    except ValueError as exc:
        raise hybrid.HybridError(str(exc), "return") from None
    return {"sale_id": sale.pk, "returned_total": str(total), "inventory_delta": delta,
            "message": "Devolución registrada y conciliada. El reintegro electrónico se realiza fuera del POS."}


def _post_view(view, actor, payload):
    request = HttpRequest()
    request.method = "POST"
    request.user = actor
    request.POST = QueryDict("", mutable=True)
    request.POST.update(payload)
    response = view().post(request)
    result = json.loads(response.content)
    if response.status_code >= 400 or not result.get("success"):
        raise hybrid.HybridError(result.get("error", "No se pudo conciliar el cierre."), "close")
    return result


def _close(session, device, data):
    _permission(session, CLOSE)
    data = normalize_close(data)
    turn = TurnoCaja.objects.select_for_update().filter(pk=session.turno_id, cajero=session.usuario,
                                                        puntopago=device.punto).first()
    if not turn or turn.pk != data["turn_id"] or turn.estado not in {"ABIERTO", "CIERRE"}:
        raise hybrid.HybridError("El turno no está disponible para este cierre.", "turn")
    if SesionHibrida.objects.filter(turno=turn, liberada_en__isnull=True).exclude(pk=session.pk).exists():
        raise hybrid.HybridError("Otro equipo aún tiene una autorización sobre este turno.", "pending")
    # La secuencia ya fue comprobada. Liberación y ambos pasos originales están
    # en la transacción del recibo: cualquier rechazo restaura la sesión/turno.
    session.liberada_en = timezone.now()
    session.save(update_fields=["liberada_en"])
    from mainApp.views import TurnoCajaIniciarCierreApi, TurnoCajaCerrarApi
    _post_view(TurnoCajaIniciarCierreApi, session.usuario, {"turno_id": str(turn.pk)})
    result = _post_view(TurnoCajaCerrarApi, session.usuario, {
        "turno_id": str(turn.pk), "efectivo_entregado": data["cash_counted"],
        "facturas_pagadas": data["bills_paid"], "ptm_transacciones": str(data["ptm_count"]),
        "medios_json": json.dumps([{"metodo": key, "contado": value} for key, value in data["methods"].items()]),
    })
    return {"turn_id": turn.pk, "closed": True, "inventory_delta": {}, "summary": result,
            "message": "Turno cerrado en la nube. No quedan operaciones anteriores sin confirmar."}


@transaction.atomic
def accept_operation(device, data):
    device = hybrid.locked_device(device)
    operation_id = hybrid.identifier(data.get("operation_id"))
    identity = fingerprint(data)
    previous = OperacionHibrida.objects.select_related("sesion").filter(pk=operation_id).first()
    if previous:
        if previous.sesion.equipo_id != device.pk or previous.huella != identity:
            raise hybrid.HybridError("Referencia usada por otra operación.", "idempotency")
        return previous.respuesta
    hybrid.require_device(device)
    session = hybrid.load_session(device, data.get("session_id"), offline_pending=True)
    hybrid.require_user(session.usuario, device)
    if (set(data) - {"protocol", "operation_id", "session_id", "sequence", "occurred_at", "kind", "data"}
            or type(data.get("protocol")) is not int or data["protocol"] != PROTOCOL
            or type(data.get("sequence")) is not int or data["sequence"] != session.secuencia + 1):
        raise hybrid.HybridError("Falta sincronizar una operación anterior o el contrato no es válido.", "sequence")
    occurred = parse_datetime(str(data.get("occurred_at", "")))
    margin = timedelta(minutes=2)
    if (occurred is None or timezone.is_naive(occurred) or not session.creada_en-margin <= occurred <= session.vence_en
            or occurred > timezone.now()+margin):
        raise hybrid.HybridError("Fecha fuera de la autorización local.", "clock")
    kind = data.get("kind")
    if kind == RETURN:
        result, operation_type = _return(session, device, data.get("data")), "return"
    elif kind == CLOSE:
        result, operation_type = _close(session, device, data.get("data")), "turn.close"
    else:
        raise hybrid.HybridError("Operación no admitida.", "kind", 400)
    result = {**result, "status": "accepted", "operation_id": str(operation_id), "kind": kind}
    OperacionHibrida.objects.create(id=operation_id, sesion=session, secuencia=data["sequence"],
        huella=identity, venta=None, tipo=operation_type, ocurrida_en=max(occurred, session.creada_en), respuesta=result)
    session.secuencia = data["sequence"]
    session.save(update_fields=["secuencia"])
    return result
