"""4 x 1.000 configurable de los pagos salientes, no de las ventas."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def expense_amounts(amount, enabled):
    """Devuelve base, impuesto y total, redondeados una sola vez a centavos."""
    from .operational_expenses import OperationalExpenseError

    try:
        base = Decimal(str(amount))
        if not base.is_finite() or base <= 0 or base >= Decimal("1000000000000"):
            raise InvalidOperation
        if base != base.quantize(Decimal("0.01")):
            raise InvalidOperation
        base = base.quantize(Decimal("0.01"))
        tax = (base * Decimal("0.004")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if enabled else Decimal("0.00")
        total = base + tax
        if total >= Decimal("1000000000000"):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError):
        raise OperationalExpenseError("El valor del pago, incluido el 4 × 1.000, debe ser positivo y caber en 12 enteros y 2 decimales.") from None
    return base, tax, total


def check_expected_tax(expected, enabled):
    from .operational_expenses import OperationalExpenseError

    if expected is not None and (not isinstance(expected, bool) or expected != enabled):
        raise OperationalExpenseError("Cambió la configuración del 4 × 1.000. Revisa de nuevo el total antes de confirmar; no se guardó el pago.")


def expense_tax_preview(methods, expense=None):
    """Al editar se conserva la regla histórica si no se cambia el medio."""
    rules = {row["code"]: bool(row.get("expense_tax_enabled", False)) for row in methods}
    if expense is not None:
        rules[expense.medio_pago] = expense.aplica_4xmil
    return rules
