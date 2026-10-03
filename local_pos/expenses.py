"""Adaptador del diario local para pagos separados del turno y del inventario."""
from mainApp.models import MetodoPago, normalizar_nombre_concepto_egreso
from mainApp.permissions import user_can_access_url_name
from mainApp.services.expense_tax import expense_amounts
from pos_shared.protocol import ProtocolError

KIND = "expense.create.v1"


def methods(session):
    values = session.data.get("expense_methods", [])
    if not isinstance(values, list):
        raise ProtocolError("Falta la autorización de medios para pagos.")
    return values


def prepare(state, session, data, using="default"):
    allowed = {"operation_id", "session_id", "concept", "amount_base", "method", "expected_tax"}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ProtocolError("Datos de pago no admitidos.")
    if not isinstance(data.get("concept"), str):
        raise ProtocolError("Escribe el concepto del pago.")
    concept = normalizar_nombre_concepto_egreso(data["concept"])
    if not concept or len(concept) > 160:
        raise ProtocolError("El concepto debe tener entre 1 y 160 caracteres.")
    method = next((item for item in methods(session) if item.get("code") == data.get("method") and item.get("active")), None)
    if (not method or not isinstance(method.get("quote"), str) or not method["quote"]
            or not MetodoPago.objects.using(using).filter(pk=data.get("method"), activo=True).exists()):
        raise ProtocolError("Ese medio de pago no está autorizado en la copia local.")
    tax_enabled = method.get("expense_tax_enabled")
    if type(tax_enabled) is not bool or type(data.get("expected_tax")) is not bool or data["expected_tax"] != tax_enabled:
        raise ProtocolError("Revisa el impuesto y el total antes de confirmar el pago.")
    try:
        base, tax, total = expense_amounts(data.get("amount_base"), tax_enabled)
    except ValueError as exc:
        raise ProtocolError(str(exc)) from None
    payload = {"concept": concept, "amount_base": str(base), "method": method["code"],
               "method_quote": method["quote"], "expected_tax": tax_enabled}
    local = {"inventory_delta": {}, "projected": True, "concept": concept, "base": str(base), "tax": str(tax),
             "total": str(total), "method": method["code"], "method_label": method["label"],
             "summary": f"{concept}: {total} ({method['label']})", "receipt": ""}
    return payload, local


def create(actor, data, remote, *, using="default"):
    if not actor.is_authenticated or not actor.is_active or not user_can_access_url_name(actor, "registrar_egreso"):
        raise ProtocolError("No tienes permiso para registrar pagos.")
    from .business import submit
    return submit(actor, KIND, data, remote, prepare, using=using)
