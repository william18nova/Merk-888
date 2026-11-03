"""Pagos inequívocos dictados/escritos: preparar, nunca ejecutar ni confirmar.

Si no se reconoce TODO el mensaje se usa el intérprete normal. No se deducen
medios desconocidos, conceptos, importes, fechas ni impuestos.
"""
import re
from decimal import Decimal

from .telegram_ai_policy import normalized


_AMOUNT = r"(?P<monto>(?:[1-9][0-9]{0,2}(?:\.[0-9]{3}){1,3}|[1-9][0-9]{0,11})(?:,[0-9]{1,2})?)"
_METHOD = r"(?P<medio>nequi|n[eé]ki|efectivo|daviplata|tarjeta|banco caja social|caja social)"
_REQUEST = re.compile(
    r"\s*(?:(?:hola|por favor)[, ]+)?"
    r"(?:(?:registra|registrar|agrega|agregar|anota|anotar)\s+(?:(?:un|este)\s+)?(?:pago\s+(?:de\s+|por\s+)?)?"
    r"|pago\s+(?:de\s+|por\s+))"
    r"\$?\s*" + _AMOUNT + r"(?:\s+pesos)?\s+(?:por|en|con)\s+" + _METHOD +
    r"\s+(?:(?:para|a|de)\s+)?(?P<concepto>.+?)\s*[.!]?\s*", re.IGNORECASE,
)


def direct_expense_request(text):
    original = str(text or "")
    if len(original) > 360:
        return None
    match = _REQUEST.fullmatch(original)
    if not match:
        return None
    concept = match["concepto"].strip()
    # Una segunda instrucción o fecha no puede ocultarse dentro del concepto.
    if not concept or len(concept) > 160 or re.search(
        r"[;\n!?]|\b(?:registra\w*|agrega\w*|anota\w*|cambia\w*|borra\w*|elimina\w*|"
        r"anula\w*|edita\w*|corrige\w*|cancela\w*|tambien|otro|otros|ayer|hoy|manana|"
        r"despues|antes|impuesto|mil|millones|separado|4x1000|no|pero|excepto|mitad|resto)\b|[0-9]", normalized(concept),
    ):
        return None
    amount = Decimal(match["monto"].replace(".", "").replace(",", "."))
    if amount <= 0 or amount >= Decimal("1000000000000"):
        return None
    method = normalized(match["medio"])
    method = {"neki": "nequi", "banco caja social": "tarjeta", "caja social": "tarjeta"}.get(method, method)
    return {"concepto": concept, "monto": str(amount), "medio_pago": method}
