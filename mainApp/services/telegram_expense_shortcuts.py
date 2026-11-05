"""Pagos inequívocos dictados/escritos: preparar, nunca ejecutar ni confirmar.

Si no se reconoce TODO el mensaje se usa el intérprete normal. No se deducen
medios desconocidos, conceptos, importes, fechas ni impuestos.
"""
import re
from decimal import Decimal

from .telegram_ai_policy import normalized


_AMOUNT = r"(?P<monto>(?:[1-9][0-9]{0,2}(?:\.[0-9]{3}){1,3}|[1-9][0-9]{0,11})(?:,[0-9]{1,2})?)"
_METHOD = r"(?P<medio>nequi|n[eé]ki|efectivo|daviplata|tarjeta|banco caja social|caja social)"
_AMOUNT_LIMIT = Decimal("1000000000000")
_POLITE = r"\s*(?:(?:hola|por favor)[, ]+)?"
_REGISTER = r"(?:registra|registrar|agrega|agregar|anota|anotar)\s+(?:(?:un|este)\s+)?"
_VALUE = r"\$?\s*" + _AMOUNT + r"(?:\s+pesos)?"
_PAID_BY = r"\s+(?:por|en|con)\s+" + _METHOD
_END = r"\s*[.!]?\s*"
_REQUEST = re.compile(
    _POLITE + rf"(?:{_REGISTER}(?:pago\s+(?:de\s+|por\s+)?)?|pago\s+(?:de\s+|por\s+))"
    + _VALUE + _PAID_BY + r"\s+(?:(?:para|a|de)\s+)?(?P<concepto>.+?)" + _END, re.IGNORECASE,
)
# Sin medio o sin concepto: solo una orden explícita de pago, no frases
# elípticas («agrega 5000»), montos dudosos, ni filtros adicionales.
_EXPLICIT = _POLITE + _REGISTER + r"pago\s+(?:de|por)\s+" + _VALUE
_MISSING_CONCEPT = re.compile(_EXPLICIT + _PAID_BY + r"(?:\s+(?:para|a|de))?" + _END, re.IGNORECASE)
_MISSING_METHOD = re.compile(_EXPLICIT + r"\s+para\s+(?P<concepto>[^\W\d_]+(?:[ -][^\W\d_]+){0,7})" + _END, re.IGNORECASE)


def _safe_concept(concept):
    # Una segunda instrucción o fecha no puede ocultarse dentro del concepto.
    if not concept or len(concept) > 160:
        return False
    query = normalized(concept)
    return not re.fullmatch(r"(?:a|de|para|por|con|en|y|o)", query) and not re.search(
        r"[;\n!?]|\b(?:registra\w*|agrega\w*|anota\w*|cambia\w*|borra\w*|elimina\w*|"
        r"anula\w*|edita\w*|corrige\w*|cancela\w*|tambien|otro|otros|ayer|hoy|manana|"
        r"despues|antes|impuesto|mil|millones|separado|4x1000|no|pero|excepto|mitad|resto|"
        r"esta semana|la semana pasada|este mes|el mes pasado)\b|[0-9]",
        query,
    )


def _parse_amount(value):
    return Decimal(value.replace(".", "").replace(",", "."))


def direct_expense_request(text):
    original = str(text or "")
    if len(original) > 360:
        return None
    match = _REQUEST.fullmatch(original)
    if not match:
        return None
    concept = match["concepto"].strip()
    if not _safe_concept(concept):
        return None
    amount = _parse_amount(match["monto"])
    if not 0 < amount < _AMOUNT_LIMIT:
        return None
    method = normalized(match["medio"])
    method = {"neki": "nequi", "banco caja social": "tarjeta", "caja social": "tarjeta"}.get(method, method)
    return {"concepto": concept, "monto": str(amount), "medio_pago": method}


def expense_clarification(text):
    """Pregunta sin proveedor ni propuesta cuando falta un solo dato inequívoco."""
    text = str(text or "")
    if len(text) > 360:
        return None
    match = _MISSING_CONCEPT.fullmatch(text)
    if match and _parse_amount(match["monto"]) < _AMOUNT_LIMIT:
        return "¿Qué concepto uso para ese pago? No lo he preparado ni registrado."
    match = _MISSING_METHOD.fullmatch(text)
    if (match and _parse_amount(match["monto"]) < _AMOUNT_LIMIT
            and _safe_concept(match["concepto"]) and not re.search(
                r"\b(?:en|por|con|sin|contra|y|o|fecha|semana|mes|transferencia|transferir|nequi|neki|"
                r"daviplata|efectivo|tarjeta|banco|usa|usando|sucursal|registrado|existente|confirmar|confirma)\b",
                normalized(match["concepto"]),
            )):
        return "¿Con qué medio pagaste? No he preparado ni registrado el pago."
    return None
