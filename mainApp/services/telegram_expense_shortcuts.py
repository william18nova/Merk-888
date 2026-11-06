"""Pagos inequívocos dictados/escritos: preparar, nunca ejecutar ni confirmar.

Si no se reconoce TODO el mensaje se usa el intérprete normal. No se deducen
medios desconocidos, conceptos, importes, fechas ni impuestos.
"""
import re
from decimal import Decimal

from .telegram_ai_policy import normalized


_COP_NUMBER = r"(?:[1-9][0-9]{0,2}(?:\.[0-9]{3}){1,3}|[1-9][0-9]{0,11})(?:,[0-9]{1,2})?"
_UNAMBIGUOUS = rf"[1-9][0-9]{{0,2}}\s+mil|{_COP_NUMBER}"
_AMBIGUOUS = r"[1-9][0-9]{0,2}(?:,[0-9]{3}){1,3}"
_METHOD_NAMES = r"nequi|n[eé]ki|efectivo|daviplata|tarjeta|banco caja social|caja social"
_METHOD = rf"(?P<medio>{_METHOD_NAMES})"
_AMOUNT_LIMIT = Decimal("1000000000000")
_POLITE = r"\s*(?:(?:hola|oye|por favor)[, ]+){0,2}"
_REGISTER = r"(?:registra|registrar|reg[ií]strame|agrega|agregar|anota|anotar|an[oó]tame)\s+(?:(?:un|una|este|esta)\s+)?"
_NOUN = r"(?:pago|compra)"
_REPORTED = r"(?:pagu[eé]|compr[eé])"
_CURRENCY = r"(?:\s+(?:pesos|cop))?"
_VALUE = rf"\$?\s*(?P<monto>{_UNAMBIGUOUS}){_CURRENCY}"
_VALUE_OR_CLARIFY = rf"\$?\s*(?P<monto>{_UNAMBIGUOUS}|{_AMBIGUOUS}){_CURRENCY}"
_VALUE_INTRO = r"(?:de|por)(?:\s+(?:un\s+)?valor\s+de)?\s+"
_AMOUNT_FIRST = _POLITE + rf"(?:{_REGISTER}(?:{_NOUN}\s+)?|(?:{_NOUN}|{_REPORTED})\s+)(?:{_VALUE_INTRO})?"
_SEPARATOR = r"(?:\s*,\s+|\s+)"
_PAYEE = rf"{_SEPARATOR}(?:(?:a favor de|para|a|de|en)\s+)?(?P<concepto>.+?)"
_PAID_BY = rf"{_SEPARATOR}(?:por|en|con)\s+{_METHOD}"
_TRAILING_METHOD = rf"{_SEPARATOR}(?:(?:y\s+)?(?:pagu[eé]|pagad[oa])\s+)?(?:por|en|con)\s+{_METHOD}"
_END = r"\s*[.!]?\s*"
_REQUESTS = tuple(re.compile(pattern + _END, re.IGNORECASE) for pattern in (
    _AMOUNT_FIRST + _VALUE_OR_CLARIFY + _PAID_BY + _PAYEE,
    _AMOUNT_FIRST + _VALUE_OR_CLARIFY + _PAYEE + _TRAILING_METHOD,
    _POLITE + rf"(?:(?:{_REGISTER})?{_NOUN}|{_REPORTED})\s+(?:en|a|para|de|por)\s+(?P<concepto>.+?)\s+"
    + _VALUE_INTRO + _VALUE_OR_CLARIFY + _TRAILING_METHOD,
))
# Sin medio o sin concepto: solo una orden explícita de pago, no frases
# elípticas («agrega 5000»), montos dudosos, ni filtros adicionales.
_EXPLICIT = _POLITE + _REGISTER + r"pago\s+(?:de|por)\s+" + _VALUE
_MISSING_CONCEPT = re.compile(_EXPLICIT + _PAID_BY + r"(?:\s+(?:para|a|de))?" + _END, re.IGNORECASE)
_MISSING_METHOD = re.compile(_EXPLICIT + r"\s+para\s+(?P<concepto>[^\W\d_]+(?:[ -][^\W\d_]+){0,7})" + _END, re.IGNORECASE)


def _safe_concept(concept):
    # Una segunda instrucción o fecha no puede ocultarse dentro del concepto.
    if len(concept) > 160 or not re.search(r"[^\W\d_]", concept):
        return False
    query = normalized(concept)
    return not re.fullmatch(r"(?:a|de|para|por|con|en|y|o)", query) and not re.search(
        r"[;$\n!?]|\b(?:registra\w*|agrega\w*|anota\w*|cambia\w*|borra\w*|elimina\w*|"
        r"anula\w*|edita\w*|corrige\w*|cancela\w*|pague|pagar|compre|comprar|tambien|otro|otros|ayer|hoy|manana|"
        r"despues|antes|impuesto|mil|millon(?:es)?|separado|4x1000|no|pero|excepto|mitad|resto|cuotas?|"
        r"lunes|martes|miercoles|jueves|viernes|sabado|domingo|esta semana|la semana pasada|este mes|el mes pasado)\b|"
        r"\bsin\s+(?:confirm\w*|registr\w*|guard\w*)\b|"
        r"\b(?:y|ademas|luego)\s+(?:dime|dame|muestra\w*|consulta\w*|busca\w*|revisa\w*)\b|"
        rf"\b(?:por|en|con|via|mediante)\s+(?:{_METHOD_NAMES}|transferencia|consignacion)\b|"
        r"(?<!\w)[0-9]+(?:[.,][0-9]+)*(?=\b|(?:pesos|cop)\b)|\b(?:y|o)\s*$",
        query,
    )


def _parse_amount(value):
    thousands = re.fullmatch(r"([1-9][0-9]{0,2})\s+mil", value, re.IGNORECASE)
    if thousands:
        return Decimal(thousands[1]) * 1000
    return Decimal(value.replace(".", "").replace(",", "."))


def _complete_request(text):
    original = str(text or "")
    if len(original) > 360:
        return None
    matches = [match for pattern in _REQUESTS if (match := pattern.fullmatch(original))
               and _safe_concept(match["concepto"].strip())]
    # Si dos órdenes posibles extraen datos distintos, no elegir uno al azar.
    if matches and len({(m["concepto"].strip(), m["monto"], m["medio"]) for m in matches}) == 1:
        return matches[0]
    return None


def _arguments(match, amount):
    if not 0 < amount < _AMOUNT_LIMIT:
        return None
    return {"concepto": match["concepto"].strip(), "monto": str(amount), "medio_pago": _method(match)}


def _method(match):
    method = normalized(match["medio"])
    return {"neki": "nequi", "banco caja social": "tarjeta", "caja social": "tarjeta"}.get(method, method)


def expense_request(text):
    """Devuelve los argumentos de preparación y si el importe necesita aclaración."""
    match = _complete_request(text)
    if not match:
        return None, False
    if re.fullmatch(_AMBIGUOUS, match["monto"]):
        return None, True
    return _arguments(match, _parse_amount(match["monto"])), False


def direct_expense_request(text):
    return expense_request(text)[0]


def complete_expense_amount(original, corrected_amount):
    match = _complete_request(original)
    if match and re.fullmatch(_AMBIGUOUS, match["monto"]):
        return _arguments(match, corrected_amount)
    return None


def incomplete_expense_fields(text):
    """Conserva solo los datos comprobables de una solicitud incompleta."""
    text = str(text or "")
    if len(text) > 360:
        return None
    match = _MISSING_CONCEPT.fullmatch(text)
    if match and _parse_amount(match["monto"]) < _AMOUNT_LIMIT:
        return {"monto": str(_parse_amount(match["monto"])), "medio_pago": _method(match)}
    match = _MISSING_METHOD.fullmatch(text)
    if (match and _parse_amount(match["monto"]) < _AMOUNT_LIMIT
            and _safe_concept(match["concepto"]) and not re.search(
                r"\b(?:en|por|con|sin|contra|y|o|fecha|semana|mes|transferencia|transferir|nequi|neki|"
                r"daviplata|efectivo|tarjeta|banco|usa|usando|sucursal|registrado|existente|confirmar|confirma)\b",
                normalized(match["concepto"]),
            )):
        return {"monto": str(_parse_amount(match["monto"])), "concepto": match["concepto"].strip()}
    return None
