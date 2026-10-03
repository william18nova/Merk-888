"""Autorización acotada de medios; no comunica con bancos ni inventa acuses."""
from django.core import signing
from mainApp.models import MetodoPago
from mainApp.services.hybrid import HybridError
from pos_shared.payments import split_payments

SALT = "nova.hybrid.payment-method.v1"


def payment_methods(session):
    rows = []
    for method in MetodoPago.objects.filter(activo=True).order_by("orden", "codigo"):
        # Estos nombres representan estados internos, nunca un medio cobrable.
        if method.codigo in {"mixto", "sin_pago", "facturas_pagadas"}:
            continue
        row = {"code": method.codigo, "label": method.nombre, "active": True,
               "is_cash": method.es_efectivo, "version": method.version}
        row["quote"] = signing.dumps({**row, "session_id": str(session.pk)}, salt=SALT)
        rows.append(row)
    return rows


def validate_payments(session, values, total, received):
    if not isinstance(values, list):
        raise HybridError("Medios de pago inválidos.", "payments", 400)
    authorized = []
    for row in values:
        try:
            method = signing.loads(row.get("quote", ""), salt=SALT)
            if method["session_id"] != str(session.pk) or method["code"] != row["medio_pago"]:
                raise ValueError
            current = MetodoPago.objects.get(pk=method["code"], activo=True)
            if current.es_efectivo != method["is_cash"]:
                raise ValueError
            authorized.append(method)
        except (signing.BadSignature, ValueError, KeyError, TypeError, AttributeError, MetodoPago.DoesNotExist):
            raise HybridError("El medio de pago no tiene una autorización vigente.", "payments", 409) from None
    return split_payments(values, total, received, authorized)
