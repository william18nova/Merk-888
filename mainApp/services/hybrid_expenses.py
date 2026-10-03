"""Pagos informativos híbridos: recibo idempotente, sin afectar caja ni turno."""
from datetime import timedelta

from django.core import signing
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from mainApp.models import ConceptoEgreso, Egreso, MetodoPago, OperacionHibrida, normalizar_nombre_concepto_egreso
from mainApp.permissions import user_can_access_url_name
from pos_shared.protocol import PROTOCOL, fingerprint
from . import hybrid
from .expense_tax import expense_amounts

SALT = "nova.hybrid.expense-method.v1"


def expense_methods(session):
    """La regla fiscal autorizada se congela para no cambiar pagos ya realizados."""
    if not user_can_access_url_name(session.usuario, "registrar_egreso"):
        return []
    return [{"code": method.pk, "label": method.nombre, "active": True,
             "expense_tax_enabled": method.aplica_4xmil_egresos,
             "quote": signing.dumps({"session": str(session.pk), "method": method.pk,
                 "tax": method.aplica_4xmil_egresos, "version": method.version}, salt=SALT)}
            for method in MetodoPago.objects.filter(activo=True).order_by("orden", "nombre", "pk")]


def expense_concepts(session):
    if not user_can_access_url_name(session.usuario, "registrar_egreso"):
        return []
    return list(ConceptoEgreso.objects.order_by("nombre").values_list("nombre", flat=True)[:5000])


@transaction.atomic
def accept_expense(device, data):
    """Una misma operación siempre devuelve el mismo pago y su mismo impuesto."""
    device = hybrid.locked_device(device)
    operation_id = hybrid.identifier(data.get("operation_id"))
    payload_hash = fingerprint(data)
    previous = OperacionHibrida.objects.select_related("sesion").filter(pk=operation_id).first()
    if previous:
        if (previous.sesion.equipo_id != device.pk or previous.huella != payload_hash
                or previous.tipo != "expense"):
            raise hybrid.HybridError("La referencia pertenece a otra operación; no se duplicó el pago.", "idempotency")
        return previous.respuesta
    hybrid.require_device(device)
    session = hybrid.load_session(device, data.get("session_id"), offline_pending=True)
    hybrid.require_user(session.usuario, device)
    if not user_can_access_url_name(session.usuario, "registrar_egreso"):
        raise hybrid.HybridError("No tienes permiso para registrar pagos.", "permission", 403)
    allowed = {"protocol", "operation_id", "session_id", "sequence", "occurred_at", "concept",
               "amount_base", "method", "method_quote", "expected_tax"}
    if (set(data) - allowed or type(data.get("protocol")) is not int or data["protocol"] != PROTOCOL
            or type(data.get("sequence")) is not int or data["sequence"] != session.secuencia + 1):
        raise hybrid.HybridError("La secuencia o los datos del pago no son válidos.", "sequence")
    occurred = parse_datetime(str(data.get("occurred_at", "")))
    margin = timedelta(minutes=2)
    if (not occurred or timezone.is_naive(occurred) or not session.creada_en-margin <= occurred <= session.vence_en
            or occurred > timezone.now()+margin):
        raise hybrid.HybridError("La fecha del pago queda fuera de la autorización local.", "clock")
    occurred = max(occurred, session.creada_en)
    concept = normalizar_nombre_concepto_egreso(data.get("concept"))
    if not isinstance(data.get("concept"), str) or not concept or len(concept) > 160:
        raise hybrid.HybridError("Escribe un concepto de entre 1 y 160 caracteres.", "concept", 400)
    try:
        quote = signing.loads(data.get("method_quote", ""), salt=SALT)
        if (quote["session"] != str(session.pk) or quote["method"] != data.get("method")
                or type(quote["tax"]) is not bool or type(data.get("expected_tax")) is not bool
                or quote["tax"] != data["expected_tax"]):
            raise ValueError
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise hybrid.HybridError("La autorización del medio o del impuesto no es válida.", "quote") from None
    method = MetodoPago.objects.select_for_update().filter(pk=quote["method"], activo=True).first()
    if not method:
        raise hybrid.HybridError("El medio de pago fue retirado; conserva este pago para revisión.", "method")
    base, tax, total = expense_amounts(data.get("amount_base"), quote["tax"])
    # Conserva la regla firmada cuando se pagó offline. No vuelve a cobrar el
    # impuesto vigente al sincronizar ni modifica el importe confirmado.
    concept_row, _ = ConceptoEgreso.objects.get_or_create(nombre=concept, defaults={"creado_por": session.usuario})
    expense = Egreso.objects.create(concepto=concept_row, monto=total, aplica_4xmil=quote["tax"],
        impuesto_4xmil=tax, medio_pago=method.pk, registrado_por=session.usuario,
        registrado_por_nombre=str(session.usuario.nombreusuario or session.usuario)[:160])
    Egreso.objects.filter(pk=expense.pk).update(creado_en=occurred)
    response = {"status": "accepted", "operation_id": str(operation_id), "expense_id": expense.pk,
                "concept": concept, "base": str(base), "tax": str(tax), "total": str(total),
                "method": method.pk, "occurred_at": occurred.isoformat()}
    OperacionHibrida.objects.create(id=operation_id, sesion=session, secuencia=data["sequence"], tipo="expense",
        huella=payload_hash, venta=None, ocurrida_en=occurred, respuesta=response)
    session.secuencia = data["sequence"]
    session.save(update_fields=["secuencia"])
    device.visto_en = timezone.now()
    device.save(update_fields=["visto_en"])
    return response
