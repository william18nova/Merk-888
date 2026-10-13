"""Validate the itemized cash payments without posting another cash movement."""
import json
import re
from decimal import Decimal


def amount(value, label="El valor"):
    raw = str(value).strip() if value is not None else ""
    if not re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,2})?", raw):
        raise ValueError(f"{label} debe ser un número de 0 o mayor, con máximo dos decimales.")
    return Decimal(raw).quantize(Decimal("0.01"))


def validate(total, details, *, allow_legacy=False):
    total = amount(total if total not in (None, "") else "0", "El total pagado")
    if details is None and allow_legacy:
        # Only old, signed offline events can use this internal option.
        # Never invent item names for payments recorded before this feature.
        return total, None
    if isinstance(details, str):
        if len(details) > 50000:
            raise ValueError("El detalle de pagos es demasiado extenso.")
        try:
            details = json.loads(details)
        except (TypeError, ValueError):
            raise ValueError("El detalle de pagos no es válido. Recarga el cierre y revisa los pagos.") from None
    if details is None:
        details = []
    if not isinstance(details, list) or len(details) > 100:
        raise ValueError("Registra como máximo 100 pagos, cada uno con nombre y valor.")
    normalized = []
    for index, row in enumerate(details, 1):
        if not isinstance(row, dict) or set(row) != {"nombre", "monto"} or not isinstance(row["nombre"], str):
            raise ValueError(f"Revisa el nombre y el valor del pago {index}.")
        name = " ".join(row["nombre"].split()).upper()
        if not name or len(name) > 160 or any(ord(char) < 32 for char in name):
            raise ValueError(f"Escribe un nombre de hasta 160 caracteres para el pago {index}.")
        value = amount(row["monto"], f"El valor del pago {index}")
        if value <= 0:
            raise ValueError(f"El pago {index} debe ser mayor que 0. Elimina los pagos vacíos.")
        normalized.append({"nombre": name, "monto": str(value)})
    summed = sum((Decimal(row["monto"]) for row in normalized), Decimal("0.00"))
    if summed != total:
        raise ValueError("La suma de cada pago debe coincidir exactamente con el total indicado al inicio. Revisa el desglose de pagos.")
    return total, normalized


def save(turn, user, total, details):
    from mainApp.models import DesglosePagosCierre
    if details is not None:
        DesglosePagosCierre.objects.update_or_create(turno=turn, defaults={
            "total": total, "pagos": details, "registrado_por": user,
        })


def payload(turn):
    from mainApp.models import DesglosePagosCierre
    record = DesglosePagosCierre.objects.select_related("registrado_por").filter(turno=turn).first()
    if record is None:
        return {"disponible": False, "pagos": []}
    return {"disponible": True, "total": str(record.total), "pagos": record.pagos,
            "registrado_por": record.registrado_por.get_username(), "registrado_en": record.registrado_en.isoformat()}
