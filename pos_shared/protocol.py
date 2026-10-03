"""Contrato v1: efectivo, cantidades enteras (gramos para productos por peso)."""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from .pricing import apply_bag_promo, round_account_peso

PROTOCOL = 1
MAX_ITEMS = 100


class ProtocolError(ValueError):
    pass


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def fingerprint(data):
    return hashlib.sha256(canonical(data).encode()).hexdigest()


def amount(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result >= Decimal("100000000") or result != result.quantize(Decimal("0.01")):
            raise InvalidOperation
        return result
    except (TypeError, ValueError, InvalidOperation):
        raise ProtocolError("Importe inválido; usa un valor positivo con hasta dos decimales.") from None


def price_cart(items, products):
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
        raise ProtocolError("El carrito debe contener entre 1 y 100 productos.")
    details, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ProtocolError("Producto inválido.")
        pid, qty = item.get("id"), item.get("quantity")
        if type(pid) is not int or type(qty) is not int or not 1 <= qty <= 1000000 or pid in seen:
            raise ProtocolError("Usa productos sin repetir y cantidades enteras entre 1 y 1.000.000 (unidades o gramos).")
        product = products.get(str(pid)) or products.get(pid)
        if product is None:
            raise ProtocolError("El producto no está disponible en el respaldo. Sincroniza el catálogo.")
        seen.add(pid)
        details.append({"productoid": pid, "producto": product["name"], "cantidad": qty,
                        "precio_unitario": amount(product["price"])})
    details, raw_total = apply_bag_promo(details)
    total = round_account_peso(raw_total)
    if total <= 0 or total >= Decimal("100000000"):
        raise ProtocolError("El total del piloto debe ser mayor que cero y menor a $100.000.000.")
    # No se consulta ni se restringe el stock: los negativos están permitidos.
    return details, total
