"""Importes repartidos por medio; un registro no verifica un pago bancario."""
from decimal import Decimal
from .protocol import ProtocolError, amount


def split_payments(values, total, cash_received, methods):
    if not isinstance(values, list) or not 1 <= len(values) <= 20:
        raise ProtocolError("Selecciona al menos un medio de pago.")
    by_code = {row["code"]: row for row in methods if row.get("active", True)}
    output, seen, cash = [], set(), Decimal("0")
    for value in values:
        if not isinstance(value, dict):
            raise ProtocolError("Medio de pago inválido.")
        code = value.get("medio_pago")
        if code not in by_code or code in seen:
            raise ProtocolError("El medio de pago no está autorizado o está repetido.")
        paid = amount(value.get("monto"))
        if paid <= 0:
            raise ProtocolError("Cada medio seleccionado debe tener un importe mayor a cero.")
        seen.add(code)
        output.append({"medio_pago": code, "monto": paid})
        if by_code[code].get("is_cash"):
            cash += paid
    if sum((row["monto"] for row in output), Decimal("0")) != total:
        raise ProtocolError("La suma de los medios de pago no coincide con el total.")
    received = amount(cash_received if cash_received not in (None, "") else cash)
    if cash == 0:
        received = Decimal("0")
    elif received < cash:
        raise ProtocolError("El efectivo recibido no cubre la parte en efectivo.")
    return output, received, received - cash
