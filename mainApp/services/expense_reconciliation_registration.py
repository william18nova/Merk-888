"""Registrar un desglose de cierre en Pagos, sin volver a tocar la caja.

La fuente cerrada y el importe mandan; el navegador solo envía una referencia
firmada y la confirmación. El bloqueo de la fuente serializa clics/reintentos.
"""
import re
from decimal import Decimal

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from .close_payments import validate
from .expense_editing import can_edit_expenses, edit_operational_expense, expense_edit_token, expense_values
from .expense_reconciliation import CO, RECONCILIATION_START, closing_report, source_version
from .expense_tax import expense_amounts
from .operational_expenses import OperationalExpenseError, register_operational_expense
from .payment_methods import payment_method_options

SALT = "mainApp.closing-expense-registration.v1"
MAX_AGE = 30 * 60


class ClosingExpenseConflict(OperationalExpenseError):
    pass


def registration_previews(rows, user):
    """Añade revisión/total y token solo a declaraciones válidas sin coincidencias."""
    eligible = [row for row in rows if row.get("status") == "missing"
                and row.get("source_version") and not row.get("source_error")
                and str(row.get("name", "")).strip() and row.get("amount", 0) > 0]
    if not eligible:
        return
    cash = next((item for item in payment_method_options(active_only=True) if item["code"] == "efectivo"), None)
    for row in eligible:
        if cash is None:
            row["registration_error"] = "El medio Efectivo está desactivado. No se puede registrar este pago."
            continue
        enabled = bool(cash.get("expense_tax_enabled", False))
        try:
            base, tax, total = expense_amounts(row["amount"], enabled)
        except OperationalExpenseError as exc:
            row["registration_error"] = str(exc)
            continue
        row["registration"] = {
            "token": signing.dumps({"user": user.pk, "turn": row["turn"], "row": row["index"],
                                    "version": row["source_version"], "cash_tax": enabled}, salt=SALT, compress=True),
            "base": base, "tax": tax, "total": total,
        }


def _token(value, user):
    try:
        data = signing.loads(value, salt=SALT, max_age=MAX_AGE)
        if (not isinstance(data, dict) or set(data) != {"user", "turn", "row", "version", "cash_tax"}
                or data["user"] != user.pk or type(data["turn"]) is not int or data["turn"] <= 0
                or type(data["row"]) is not int or not 0 <= data["row"] < 100
                or type(data["cash_tax"]) is not bool
                or not isinstance(data["version"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["version"])):
            raise ValueError
    except (signing.BadSignature, TypeError, ValueError, OverflowError):
        raise ClosingExpenseConflict("La revisión venció o no es válida. Recarga los cierres y revisa el pago de nuevo.") from None
    return data


def _existing_link(turn_id, row_index):
    from mainApp.models import CambioEgreso
    # Incluye las conciliaciones auditadas anteriores a este botón, aunque el
    # pago se haya movido después a otra fecha y no aparezca en el rango actual.
    audits = CambioEgreso.objects.select_related("egreso").filter(
        motivo__startswith="CONCILIACION", motivo__contains=f"CIERRE {turn_id}",
    ).order_by("pk")
    for audit in audits:
        match = re.search(r"\bCIERRE (\d+)(?:/(\d+))?(?:/\d+)?(?=\s|\||$)", audit.motivo)
        if match and (int(match[1]), int(match[2] or 0)) == (turn_id, row_index):
            return audit.egreso
    return None


@transaction.atomic
def register_closing_expense(*, user, token, confirmed):
    from mainApp.models import CambioEgreso, DesglosePagosCierre, MetodoPago, TurnoCaja
    from local_pos.request_context import offline_request
    if not can_edit_expenses(user):
        raise PermissionDenied("No tienes permiso para registrar pagos desde cierres.")
    if offline_request():
        raise OperationalExpenseError("Esta operación requiere conexión con la base online; no se guardó ningún pago.")
    if confirmed is not True:
        raise OperationalExpenseError("Confirma que revisaste los pagos existentes y que este pago falta por registrar.")
    data = _token(token, user)
    turn = TurnoCaja.objects.select_for_update().filter(pk=data["turn"]).first()
    if turn is None:
        raise ClosingExpenseConflict("El cierre ya no está disponible. No se guardó ningún pago.")
    source = DesglosePagosCierre.objects.select_for_update().filter(turno_id=data["turn"]).first()
    if source is None:
        raise ClosingExpenseConflict("El desglose del cierre ya no está disponible. No se guardó ningún pago.")
    source.turno = turn
    local_start = timezone.localtime(turn.inicio, CO)
    if turn.estado != "CERRADO" or local_start.date() < RECONCILIATION_START or not 12 <= local_start.hour < 20:
        raise ClosingExpenseConflict("Solo se pueden registrar declaraciones de cierres de tarde desde el 01/11/2026.")
    if data["version"] != source_version(source):
        raise ClosingExpenseConflict("El cierre cambió desde que lo revisaste. Recarga la página antes de registrar el pago.")
    try:
        _total, payments = validate(source.total, source.pagos)
        payment = payments[data["row"]]
    except (ValueError, TypeError, IndexError):
        raise ClosingExpenseConflict("El detalle o la suma del cierre no son válidos. Revisa el cierre antes de registrar pagos.") from None
    existing = _existing_link(turn.pk, data["row"])
    if existing is not None:
        return existing, False
    # La ruta normal de Pagos/Telegram también bloquea este medio antes de
    # crear un egreso: comprobar coincidencias después evita la carrera habitual.
    MetodoPago.objects.select_for_update().filter(pk="efectivo").first()
    report = closing_report(local_start.date(), local_start.date())
    current = next((row for row in report["rows"] if row["turn"] == turn.pk and row["index"] == data["row"]), None)
    if current is None or current["status"] != "missing":
        raise ClosingExpenseConflict(
            "Apareció un vínculo o una posible coincidencia con otro pago. No se creó un duplicado; revisa los pagos registrados."
        )
    expense = register_operational_expense(user=user, concept=payment["nombre"],
        amount=Decimal(payment["monto"]), payment_method="efectivo", expected_tax=data["cash_tax"])
    before = expense_values(expense)
    reason = f"CONCILIACION WEB CIERRE {turn.pk}/{data['row']} | Alta desde declaración del cierre; fecha del turno."
    expense, changed = edit_operational_expense(user=user, expense_id=expense.pk,
        concept=payment["nombre"], amount=Decimal(payment["monto"]), payment_method="efectivo",
        payment_date=local_start.date(), reason=reason, version=expense_edit_token(expense, user),
        expected_tax=data["cash_tax"])
    if not changed:
        CambioEgreso.objects.create(egreso=expense, usuario=user,
            usuario_nombre=(getattr(user, "nombreusuario", "") or str(user))[:160],
            motivo=reason, anterior=before, nuevo=expense_values(expense))
    return expense, True
