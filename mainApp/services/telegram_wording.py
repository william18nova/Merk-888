"""Texto de presentación: no consulta datos, cambia importes ni llama a la IA."""

from datetime import timedelta
import unicodedata


CONVERSATION_STYLE = (
    "Tono: habla de tú, en español colombiano natural, cercano y respetuoso, sin jerga ni confianza forzada. "
    "Ve directo a lo pedido: una o dos frases para algo sencillo, una lista clara si pide detalles. "
    "Responde primero el dato o la respuesta concreta, sin introducciones como 'Claro, aquí tienes' ni repetir la pregunta. "
    "No añadas análisis, promedios, conteos, desgloses, recomendaciones ni explicaciones que no se hayan pedido. "
    "Si pide solo un total, devuelve ese total con el período y los filtros necesarios para identificarlo. "
    "Si pide una lista, da la lista; no la sustituyas por un resumen ni omitas condiciones para acortarla. "
    "Si pide una explicación detallada, sí desarrolla lo necesario. Una respuesta breve no debe perder información solicitada. "
    "Conserva el nivel de detalle en continuaciones como 'y ayer', salvo que pida cambiarlo. "
    "No repitas saludos, disculpas, emojis ni preguntas de seguimiento en cada respuesta. "
    "Si falta información, pregunta de forma cotidiana: '¿De qué venta quieres hacer la devolución?' "
    "o '¿Cuánto pagaste y con qué medio?'; no nombres herramientas, parámetros, JSON ni códigos internos. "
    "No menciones proveedores de IA ni errores técnicos salvo que el usuario pida una explicación técnica. "
    "Conserva exactamente importes, fechas, cantidades e identificadores verificados; no inventes ni redondees datos. "
    "Distingue los totales del negocio de los pagos de una persona; no atribuyas a alguien pagos ajenos. "
    "Nunca inventes resultados ni afirmes que guardaste, enviaste dinero o completaste una acción sin confirmación del sistema. "
    "Ser cercano no cambia los permisos ni sustituye el botón Confirmar."
)


def page_note(page, pages):
    """La navegación solo necesita ocupar espacio cuando hay más de una página."""
    return f" · página {page} de {pages}" if pages > 1 else ""


def filter_label(label, operator, value):
    words = {"igual": "", "distinto": "excepto ", "contiene": "contiene ",
             "mayor": "más de ", "menor": "menos de ", "al_menos": "desde ", "hasta": "hasta "}
    return f"{label}: {words[operator]}{value}"


def period_phrase(start, end, today):
    """Usa fechas explícitas incluso cuando dice hoy/ayer, para futuras consultas."""
    if start != end:
        return f"del {start:%d/%m/%Y} al {end:%d/%m/%Y}"
    if start == today:
        return f"hoy ({start:%d/%m/%Y})"
    if start == today - timedelta(days=1):
        return f"ayer ({start:%d/%m/%Y})"
    return f"el {start:%d/%m/%Y}"


def social_reply(text):
    """Solo coincidencias completas: un saludo con una petición no oculta la petición."""
    text = "".join(c for c in unicodedata.normalize("NFKD", str(text).lower()) if not unicodedata.combining(c))
    text = " ".join(text.split()).strip(" ¿?¡!.,")
    greetings = {"hola", "hola jarvis", "buenos dias", "buenas tardes", "buenas noches", "buenas"}
    thanks = {"gracias", "muchas gracias", "gracias jarvis", "muchas gracias jarvis"}
    if text in greetings:
        return "Hola. ¿Qué necesitas revisar o hacer hoy?"
    if text in thanks:
        return "Con gusto."
    if text in {"adios", "hasta luego", "chao"}:
        return "Hasta luego."
    return None
