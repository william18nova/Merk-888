"""Correcciones autorizadas y auditadas de pagos, independientes de la caja."""
from datetime import date, datetime, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation

from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.utils import timezone

from mainApp.models import CambioEgreso, ConceptoEgreso, Egreso, MetodoPago, normalizar_nombre_concepto_egreso
from mainApp.permissions import is_web_master_role, user_has_permission
from mainApp.services.operational_expenses import OperationalExpenseError
from mainApp.services.payment_methods import normalize_payment_method_code, payment_method_label

VERSION_SALT = "mainApp.expense-edit.v1"


class ExpenseEditConflict(OperationalExpenseError):
    pass


def can_edit_expenses(user):
    return bool(
        getattr(user, "is_authenticated", False) and getattr(user, "is_active", False)
        and (is_web_master_role(user) or user_has_permission(user, "pagos_editar"))
    )


def expense_editing_ready():
    tables = connection.introspection.table_names()
    return all(model._meta.db_table in tables for model in (Egreso, ConceptoEgreso, CambioEgreso))


def expense_values(expense):
    local_date = timezone.localtime(expense.creado_en, timezone.get_default_timezone()).date()
    return {
        "conceptoid": expense.concepto_id,
        "concepto": expense.concepto.nombre,
        "monto": str(expense.monto.quantize(Decimal("0.01"))),
        "medio_pago": expense.medio_pago,
        "fecha_pago": local_date.isoformat(),
        "fecha_pago_texto": local_date.strftime("%d/%m/%Y"),
        "creado_en": expense.creado_en.astimezone(datetime_timezone.utc).isoformat(),
    }


def _version_state(expense, user):
    return {
        "id": expense.pk, "user": user.pk, "values": expense_values(expense),
        "revision": expense.cambios.order_by("-pk").values_list("pk", flat=True).first(),
    }


def expense_edit_token(expense, user):
    return signing.dumps(_version_state(expense, user), salt=VERSION_SALT, compress=True)


@transaction.atomic
def edit_operational_expense(*, user, expense_id, concept, amount, payment_method, reason, version, payment_date=None):
    if not can_edit_expenses(user):
        raise PermissionDenied("No tienes permiso para editar pagos.")
    # La misma fila serializa las ediciones; el historial y el pago se guardan juntos.
    expense = Egreso.objects.select_for_update().get(pk=expense_id)
    try:
        submitted = signing.loads(version, salt=VERSION_SALT, max_age=12 * 60 * 60)
    except (signing.BadSignature, TypeError, ValueError):
        raise ExpenseEditConflict("El formulario venció o no es válido. Recarga el pago antes de corregirlo.")
    if submitted != _version_state(expense, user):
        raise ExpenseEditConflict("Este pago fue modificado mientras lo editabas. Recarga la página y revisa los valores actuales antes de guardar.")

    concept = normalizar_nombre_concepto_egreso(concept)
    reason = " ".join(str(reason or "").split())
    if not concept or len(concept) > 160:
        raise OperationalExpenseError("El concepto debe tener entre 1 y 160 caracteres.")
    if not reason or len(reason) > 300:
        raise OperationalExpenseError("Escribe un motivo de corrección de hasta 300 caracteres.")
    try:
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount <= 0 or amount >= Decimal("1000000000000"):
            raise InvalidOperation
        rounded = amount.quantize(Decimal("0.01"))
        if rounded != amount:
            raise InvalidOperation
        amount = rounded
    except (InvalidOperation, TypeError, ValueError):
        raise OperationalExpenseError("Escribe un valor positivo válido, con máximo dos decimales.")

    method = expense.medio_pago if payment_method == expense.medio_pago else normalize_payment_method_code(payment_method)
    if method != expense.medio_pago and not MetodoPago.objects.select_for_update().filter(pk=method, activo=True).exists():
        raise OperationalExpenseError("Ese medio de pago está desactivado. Selecciona otro.")

    before = expense_values(expense)
    new_timestamp = expense.creado_en
    if payment_date not in (None, ""):
        try:
            if isinstance(payment_date, str):
                payment_date = date.fromisoformat(payment_date)
            if not isinstance(payment_date, date) or isinstance(payment_date, datetime):
                raise ValueError
            local_timestamp = timezone.localtime(expense.creado_en, timezone.get_default_timezone())
            if payment_date != local_timestamp.date():
                new_timestamp = timezone.make_aware(
                    datetime.combine(payment_date, local_timestamp.time()),
                    timezone.get_default_timezone(),
                )
                # Valida también el rango al convertir a UTC para la base de datos.
                new_timestamp.astimezone(datetime_timezone.utc)
        except (TypeError, ValueError, OverflowError):
            raise OperationalExpenseError("Selecciona una fecha de pago válida.") from None
    if (concept == before["concepto"] and str(amount) == before["monto"]
            and method == before["medio_pago"] and new_timestamp == expense.creado_en):
        return expense, False
    target, _created = ConceptoEgreso.objects.get_or_create(nombre=concept, defaults={"creado_por": user})
    expense.concepto = target
    expense.monto = amount
    expense.medio_pago = method
    # Las métricas usan esta fecha. Se conserva la hora local y la autoría;
    # el historial guarda ambas fechas y el momento real de la corrección.
    expense.creado_en = new_timestamp
    expense.save(update_fields=["concepto", "monto", "medio_pago", "creado_en"])
    after = expense_values(expense)
    for values in (before, after):
        values["medio_nombre"] = payment_method_label(values["medio_pago"])
    CambioEgreso.objects.create(
        egreso=expense, usuario=user,
        usuario_nombre=(getattr(user, "nombreusuario", "") or str(user))[:160],
        motivo=reason, anterior=before, nuevo=after,
    )
    return expense, True
