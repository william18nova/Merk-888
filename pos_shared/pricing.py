"""Reglas de precios comunes al POS web y al cliente híbrido. Sin Django."""
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR


def _decimal(value):
    # Conservar la tolerancia histórica del POS web. El protocolo híbrido
    # valida estrictamente sus precios antes de invocar esta regla compartida.
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else Decimal("0")
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def round_account_peso(value):
    amount = _decimal(value)
    return amount.quantize(Decimal("1"), rounding=ROUND_CEILING if amount > 0 else ROUND_FLOOR)


def apply_bag_promo(detalles):
    BAG_21 = 7318
    BAG_8001 = 8001
    BLOCK_VALUE = Decimal("11000")

    normalized = []
    for d in detalles or []:
        pid = int(d.get("productoid") or 0)
        qty = int(d.get("cantidad") or 0)
        price = _decimal(d.get("precio_unitario") or 0)
        nombre = d.get("producto") or f"Producto {pid}"
        normalized.append({
            "productoid": pid,
            "producto": nombre,
            "cantidad": qty,
            "precio_unitario": price,
        })

    promo_base = sum(
        (d["precio_unitario"] * d["cantidad"])
        for d in normalized
        if d["productoid"] not in {BAG_21, BAG_8001} and d["cantidad"] > 0 and d["precio_unitario"] > 0
    )
    blocks = int(promo_base // BLOCK_VALUE) if promo_base > 0 else 0

    qty_21 = sum(d["cantidad"] for d in normalized if d["productoid"] == BAG_21 and d["cantidad"] > 0)
    qty_8001 = sum(d["cantidad"] for d in normalized if d["productoid"] == BAG_8001 and d["cantidad"] > 0)

    price_21 = next((d["precio_unitario"] for d in normalized if d["productoid"] == BAG_21 and d["precio_unitario"] > 0), Decimal("0"))
    price_8001 = next((d["precio_unitario"] for d in normalized if d["productoid"] == BAG_8001 and d["precio_unitario"] > 0), Decimal("0"))

    free_21 = 0
    free_8001 = 0
    remaining_21 = qty_21
    remaining_8001 = qty_8001

    while blocks > 0 and (remaining_21 > 0 or remaining_8001 > 0):
        value_21 = (min(2, remaining_21) * price_21) if remaining_21 > 0 and price_21 > 0 else Decimal("-1")
        value_8001 = price_8001 if remaining_8001 > 0 and price_8001 > 0 else Decimal("-1")
        if value_21 <= 0 and value_8001 <= 0:
            break
        if value_8001 > value_21:
            free_8001 += 1
            remaining_8001 -= 1
        else:
            take_21 = min(2, remaining_21)
            free_21 += take_21
            remaining_21 -= take_21
        blocks -= 1

    remaining_free = {BAG_21: free_21, BAG_8001: free_8001}
    final_detalles = []
    total = Decimal("0")

    for d in normalized:
        pid = d["productoid"]
        qty = d["cantidad"]
        precio = d["precio_unitario"]
        nombre = d["producto"]

        if pid in remaining_free and qty > 0 and remaining_free[pid] > 0:
            free_qty = min(qty, remaining_free[pid])
            paid_qty = qty - free_qty
            if paid_qty > 0:
                subtotal = precio * paid_qty
                total += subtotal
                final_detalles.append({
                    "productoid": pid,
                    "producto": nombre,
                    "cantidad": paid_qty,
                    "precio_unitario": precio,
                    "subtotal": subtotal,
                })
            final_detalles.append({
                "productoid": pid,
                "producto": f"{nombre} (PROMO)",
                "cantidad": free_qty,
                "precio_unitario": Decimal("0"),
                "subtotal": Decimal("0"),
            })
            remaining_free[pid] -= free_qty
            continue

        subtotal = precio * qty
        total += subtotal
        final_detalles.append({
            "productoid": pid,
            "producto": nombre,
            "cantidad": qty,
            "precio_unitario": precio,
            "subtotal": subtotal,
        })

    return final_detalles, total

