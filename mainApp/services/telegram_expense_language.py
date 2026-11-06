"""Extrae un pago de palabras del usuario; no autoriza ni registra cambios."""
import re
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal

from .telegram_ai_output import AIOutputError
from .telegram_ai_policy import normalized
from .telegram_expense_shortcuts import _AMOUNT_LIMIT, _UNAMBIGUOUS, _parse_amount, _safe_concept


PAYMENT_FIELDS = ("monto", "concepto", "medio_pago")
PAYMENT_DIALOGUE = "aclarar_pago_datos"
PAYMENT_QUESTIONS = {
    "monto": "¿Cuántos pesos pagaste? Puedes responder solo con el importe.",
    "concepto": "¿Qué concepto uso para ese pago? Puedes decir el nombre del negocio.",
    "medio_pago": "¿Con qué medio pagaste?",
}


@dataclass(frozen=True)
class PaymentContext:
    text: str
    draft: dict | None = None


@dataclass(frozen=True)
class PaymentExtraction:
    arguments: dict
    question: str = ""


payment_context = ContextVar("telegram_payment_context", default=None)


def payment_language(text):
    """Señal de tema, no de autorización; permite narraciones, no solo órdenes."""
    query = normalized(text)
    if re.search(r"\b(?:pague|pagamos|compre|compramos|gaste|gastamos|consigne|transferi|gire|desembolse)\b", query):
        return True
    if re.search(r"\b(?:pago|compra|egreso|gasto)\b", query) and re.search(
        r"\b(?:anot\w*|apunt\w*|registr\w*|agreg\w*|guard\w*|carg\w*|met\w*|hecho|hice|salio|salieron)\b|\d",
        query,
    ):
        return True
    return bool(re.search(r"\b(?:le pase|le mande|puse|salio|salieron|anotalo|apuntalo|registralo|apuntame)\b", query)
                and re.search(r"\b(?:pesos|cop|mil|millon(?:es)?|nequi|neki|daviplata|efectivo|tarjeta|caja social)\b|\$", query))


def payment_definition(definition):
    return {
        "name": definition["name"],
        "description": "Extrae un solo pago operativo que la persona ya hizo y quiere anotar. "
                       "Nunca sirve para transferir, consultar, modificar un pago anterior ni registrar varios. "
                       "Copia literalmente solo lo dicho en el mensaje NUEVO. Omite cada dato que falte; "
                       "el servidor pregunta por él y prepara sus botones, no escribas tú una confirmación.",
        "parameters": {"type": "OBJECT", "properties": {
            "concepto": {"type": "STRING", "description": "Fragmento exacto con el concepto, negocio o proveedor. No inventes ni completes el nombre."},
            "monto_texto": {"type": "STRING", "description": "Importe base literal, incluidas las palabras de magnitud o separadores: '85.900', '90 mil', 'ochenta y cinco mil novecientos'. No lo conviertas ni calcules."},
            "medio_texto": {"type": "STRING", "description": "Palabras exactas con que dijo el medio utilizado. No adivines siglas ni cambies el medio."},
        }},
    }


def payment_prompt(continuation):
    instructions = (
        "Para registrar una compra o pago YA REALIZADO interpreta la intención, no el orden ni una frase fija. "
        "'A Comercial Sol le pasé 85.900 con Nequi, anótalo' y 'La compra en Comercial Sol fueron ochenta y cinco mil "
        "novecientos, los pagué con Nequi' solicitan preparar ese pago. Usa preparar_registro_pago con los fragmentos "
        "literales del mensaje nuevo. Copia la expresión numérica completa, incluidas palabras como 'mil'. Si falta un "
        "dato usa la función con los campos que sí estén claros. No digas que ya registraste, no inventes un botón "
        "y no sumes impuestos. Si pide transferir, consultar, editar, registrar más de un pago o mezcla acciones, "
        "no lo conviertas en un pago nuevo: usa la función pertinente o pregunta qué quiere hacer primero. "
        "Las cifras, los nombres y los medios de otros mensajes no rellenan campos nuevos."
    )
    if continuation:
        instructions += (
            " Hay una pregunta reciente del servidor por un dato de un pago sin registrar. Si el mensaje nuevo "
            "la responde, usa preparar_registro_pago solo con los campos nuevos. El servidor conserva los datos "
            "validados de esa pregunta. Si cambió de tema o empezó otro pago, no los mezcles."
        )
    return instructions


_SMALL = dict(zip(
    "cero un dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciseis "
    "diecisiete dieciocho diecinueve veinte veintiuno veintidos veintitres veinticuatro veinticinco "
    "veintiseis veintisiete veintiocho veintinueve".split(), range(30),
))
_SMALL.update(uno=1, una=1, veintiun=21, veintiuna=21)
_TENS = dict(zip("treinta cuarenta cincuenta sesenta setenta ochenta noventa".split(), range(30, 100, 10)))
_HUNDREDS = dict(zip("cien doscientos trescientos cuatrocientos quinientos seiscientos setecientos ochocientos novecientos".split(), range(100, 1000, 100)))
_HUNDREDS["ciento"] = 100
_CARDINALS = frozenset(_SMALL) | frozenset(_TENS) | frozenset(_HUNDREDS) | {"mil", "millon", "millones"}
_NUMERIC_TOKEN = re.compile(r"(?<!\w)[0-9]+(?:[.,][0-9]+)*(?:\s+(?:mil|mill[oó]n(?:es)?))?(?!\w|[.,][0-9])", re.IGNORECASE)
_CARDINAL_EDGE = "|".join(sorted(_CARDINALS - {"un", "una"}, key=lambda token: (-len(token), token)))


def _under_thousand(words):
    if not words:
        return 0
    first, *rest = words
    hundreds = _HUNDREDS.get(first, 0)
    if hundreds:
        if first == "cien" or not rest:
            return hundreds if not rest and first != "ciento" else None
        tail = _under_thousand(rest) if rest[0] not in _HUNDREDS else None
        return hundreds + tail if tail else None
    if first in _SMALL and not rest:
        return _SMALL[first]
    if first in _TENS:
        if not rest:
            return _TENS[first]
        if len(rest) == 2 and rest[0] == "y" and 0 < _SMALL.get(rest[1], 0) < 10:
            return _TENS[first] + _SMALL[rest[1]]
    return None


def _under_million(words):
    if "mil" not in words:
        return _under_thousand(words)
    i = words.index("mil")
    before = _under_thousand(words[:i]) if i else 1
    after = _under_thousand(words[i + 1:])
    return before * 1000 + after if before and after is not None else None


def parse_payment_amount(literal):
    """COP exactos. No elegir entre decimales y miles ni sumar importes."""
    if len(literal) > 140:
        return None
    value = re.sub(r"\s+", " ", normalized(literal)).strip()
    value = re.sub(r"(?:\s+(?:pesos(?: colombianos)?|cop))$", "", value).lstrip("$ ")
    scale = re.fullmatch(r"([1-9][0-9]{0,5}) (mil|millon|millones)", value)
    if scale:
        units, magnitude = int(scale[1]), scale[2]
        if (magnitude == "millon" and units != 1) or (magnitude == "millones" and units == 1):
            return None
        amount = Decimal(units) * (1000 if magnitude == "mil" else 1000000)
    elif re.fullmatch(_UNAMBIGUOUS, value):
        amount = _parse_amount(value)
    else:
        words = value.split()
        if not words or len(words) > 18 or any(word not in _CARDINALS and word != "y" for word in words):
            return None
        million = [i for i, word in enumerate(words) if word in {"millon", "millones"}]
        if not million:
            parsed = _under_million(words)
        elif len(million) == 1:
            i = million[0]
            before, after = _under_million(words[:i]), _under_million(words[i + 1:])
            parsed = before * 1000000 + after if before and after is not None and (before == 1) == (words[i] == "millon") else None
        else:
            parsed = None
        amount = Decimal(parsed) if parsed is not None else None
    return amount if amount is not None and 0 < amount < _AMOUNT_LIMIT else None


def spoken_amount_reply(text):
    value = re.sub(r"^\s*(?:son|fueron|el monto es|el valor es|monto(?: correcto)?:)\s+", "", text, flags=re.IGNORECASE)
    return parse_payment_amount(value.strip().rstrip(".!"))


def may_continue_payment(text):
    # Un borrador no se hereda si se inicia otro trámite o se da otra orden.
    return len(text) <= 240 and not re.search(
        r"[?¿;]|\b(?:otro|otra|nuevo|nueva|aparte|olvida\w*|cancela\w*|cambia\w*|corrige\w*|"
        r"registra\w*|anota\w*|apunta\w*|compra|compre|consulta\w*|ventas?|saldo|cuanto)\b|"
        r"\b(?:hice|realice|quiero|necesito)\s+(?:un |el )?(?:pago|egreso|gasto)\b|"
        r"^\s*(?:a|para)\s+.+\b(?:le )?(?:pague|pase|transferi)\b",
        normalized(text),
    )


def _literal_match(text, value):
    pattern = r"\s+".join(re.escape(word) for word in value.split())
    return re.search(rf"(?<![\w.,+\-]){pattern}(?!\w|[.,][0-9])", text, re.IGNORECASE)


def _literal_amount(text, match, concept_match):
    # No aceptar «85» en «85 mil» ni «cinco mil» en «ochenta y cinco mil».
    before, after = normalized(text[:match.start()]), normalized(text[match.end():])
    if re.search(r"[0-9][.,]\s*$", before) or re.search(r"[+\-−]\s*$", before):
        return None
    if (re.search(rf"\b(?:{_CARDINAL_EDGE})(?:\s+(?:y|o|coma|punto))?\s+$", before)
            or re.match(rf"\s*(?:(?:y|o|coma|punto)\s+)?(?:{_CARDINAL_EDGE})\b", after)):
        return None
    if (re.search(r"\b(?:id|nit|folio|referencia|numero)\s*#?\s*$", before)
            or re.match(r"\s*(?:centavos|dolares|euros|usd|eur|mxn|ars|clp|brl|pesos (?:mexicanos|argentinos|chilenos))\b", after)):
        return None
    if concept_match and concept_match.start() < match.end() and match.start() < concept_match.end():
        return None
    for other in _NUMERIC_TOKEN.finditer(text):
        if match.start() <= other.start() and other.end() <= match.end():
            continue
        if concept_match and concept_match.start() <= other.start() and other.end() <= concept_match.end():
            continue
        # Una segunda cifra que no es parte del negocio puede ser otro pago o una alternativa.
        return None
    remainder = list(text)
    for field in (match, concept_match):
        if field:
            remainder[field.start():field.end()] = " " * (field.end() - field.start())
    if re.search(rf"\b(?:{_CARDINAL_EDGE})\s+(?:mil|millon(?:es)?|pesos)\b", normalized("".join(remainder))):
        return None
    return parse_payment_amount(match.group())


def _without_match(text, match):
    return text if match is None else text[:match.start()] + " " + text[match.end():]


def extract_payment(arguments, context):
    fields = {"concepto", "monto_texto", "medio_texto"}
    if set(arguments) - fields or any(not isinstance(value, str) or not 0 < len(value.strip()) <= 160 for value in arguments.values()):
        raise AIOutputError("schema")
    text = context.text
    query = normalized(text)
    if re.search(
        r"\b(?:ayer|anteayer|manana|la semana pasada|el mes pasado|(?:el|del) (?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)|"
        r"[0-9]{1,2} de (?:enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)|"
        r"[0-9]{1,2}/[0-9]{1,2}(?:/[0-9]{2,4})?)\b", query,
    ):
        return PaymentExtraction({}, "El bot registra con la fecha de hoy. No anoté este pago con una fecha distinta.")
    if re.match(r"\s*[¿¡]?\s*(?:dime\s+)?(?:cuanto|cuantos|muestrame|consulta|busca)\b", query):
        return PaymentExtraction({}, "¿Quieres consultar el pago o anotar uno nuevo que ya hiciste? No he registrado nada.")
    if re.match(r"\s*(?:por favor[, ]+)?(?:paga|transfiere|envia|mandale)\b", query) or re.search(
        r"\b(?:(?:no|nunca)\s+(?:(?:lo|la|me|quiero que|vuelvas a)\s+){0,3}(?:registr\w*|anot\w*|guard\w*|pague|compre)|"
        r"ya (?:lo )?(?:registre|anote|guarde)|(?:voy a|tengo que|debo|cuando) (?:pagar|comprar|transferir)|"
        r"(?:haz|realiza|ejecuta) (?:un |el )?pago|"
        r"(?:dos|tres|varios|ambos|los dos) (?:pagos|egresos|gastos|compras)|"
        r"(?:y|ademas|luego) (?:registra|anota|agrega|paga|pague|compra|compre|dime|muestra|consulta))\b",
        query,
    ):
        return PaymentExtraction({}, "¿Quieres anotar un único pago ya realizado, o necesitas consultar o corregir uno existente? No he registrado nada.")
    # Solo se heredan campos de un borrador de la pregunta inmediatamente anterior.
    result = dict(context.draft or {})
    matches = {key: _literal_match(text, value.strip()) for key, value in arguments.items()}
    concept = matches.get("concepto")
    method = matches.get("medio_texto")
    if concept and (not _safe_concept(concept.group()) or re.fullmatch(
        r"(?:(?:la|el|un|una) )?(?:pago|compra|egreso|empresa|negocio|proveedor)", normalized(concept.group()),
    ) or method and concept.start() < method.end() and method.start() < concept.end()):
        concept = None
    if context.draft is not None:
        # Si el mensaje trae una corrección que el modelo omitió, no seguir
        # con el dato anterior. La respuesta siguiente debe resolverla.
        remainder = normalized(_without_match(text, concept))
        if "monto_texto" not in arguments and (_NUMERIC_TOKEN.search(remainder) or re.search(
            rf"\b(?:{_CARDINAL_EDGE})\s+(?:mil|millon(?:es)?|pesos)\b", remainder,
        )):
            result.pop("monto", None)
        if "medio_texto" not in arguments and re.search(
            r"\b(?:(?:por|en|con)\s+(?:nequi|neki|efectivo|daviplata|tarjeta|(?:banco )?caja social)|medio (?:correcto|era|fue))\b",
            remainder,
        ):
            result.pop("medio_pago", None)
        if "concepto" not in arguments and re.search(r"\bconcepto (?:correcto|era|fue|es)\b", remainder):
            result.pop("concepto", None)
    if "concepto" in arguments:
        result.pop("concepto", None)
        if concept:
            result["concepto"] = concept.group().strip()
    if "monto_texto" in arguments:
        result.pop("monto", None)
        match = matches["monto_texto"]
        amount = _literal_amount(text, match, concept) if match else None
        if amount is not None:
            result["monto"] = str(amount)
    if "medio_texto" in arguments:
        result.pop("medio_pago", None)
        if method:
            result["medio_pago"] = method.group().strip()
    return PaymentExtraction(result)
