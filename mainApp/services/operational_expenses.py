from decimal import Decimal, InvalidOperation

from django.db import transaction

from mainApp.models import (
    ConceptoEgreso,
    Egreso,
    MetodoPago,
    normalizar_nombre_concepto_egreso,
)
from mainApp.services.payment_methods import (
    DEFAULT_PAYMENT_METHODS,
    normalize_payment_method_code,
    payment_method_table_ready,
)


class OperationalExpenseError(ValueError):
    pass


def register_operational_expense(*, user, concept, amount, payment_method):
    """Única ruta de dominio para registrar pagos desde web o Telegram."""

    concept_name = normalizar_nombre_concepto_egreso(concept)
    if not concept_name or len(concept_name) > 160:
        raise OperationalExpenseError(
            "El concepto debe tener entre 1 y 160 caracteres."
        )
    try:
        normalized_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise OperationalExpenseError("El valor pagado no es válido.")
    if normalized_amount <= 0:
        raise OperationalExpenseError("El valor pagado debe ser mayor que cero.")
    if normalized_amount >= Decimal("1000000000000"):
        raise OperationalExpenseError("El valor pagado es demasiado grande.")

    method = normalize_payment_method_code(payment_method)
    if not method:
        raise OperationalExpenseError("Selecciona un medio de pago.")

    with transaction.atomic():
        if payment_method_table_ready():
            method_is_active = (
                MetodoPago.objects
                .select_for_update()
                .filter(pk=method, activo=True)
                .exists()
            )
        else:
            method_is_active = method in {
                row["code"]
                for row in DEFAULT_PAYMENT_METHODS
                if row["active"]
            }
        if not method_is_active:
            raise OperationalExpenseError(
                "Ese medio de pago está desactivado. Selecciona otro."
            )

        expense_concept, _created = ConceptoEgreso.objects.get_or_create(
            nombre=concept_name,
            defaults={"creado_por": user},
        )
        expense = Egreso.objects.create(
            concepto=expense_concept,
            monto=normalized_amount,
            medio_pago=method,
            registrado_por=user,
            registrado_por_nombre=(
                getattr(user, "nombreusuario", "")
                or str(user)
            )[:160],
        )
    return expense
