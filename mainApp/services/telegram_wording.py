"""Texto de presentación: no consulta datos, cambia importes ni llama a la IA."""

from datetime import timedelta
import unicodedata


CONVERSATION_STYLE = (
    "Tono: habla de tú, en español colombiano natural, cercano y respetuoso, sin jerga ni confianza forzada. "
    "Ve directo a lo pedido: una o dos frases para algo sencillo, una lista clara si pide detalles. "
    "No repitas saludos, disculpas, emojis ni preguntas de seguimiento en cada respuesta. "
    "Si falta información, pregunta de forma cotidiana: '¿De qué venta quieres hacer la devolución?' "
    "o '¿Cuánto pagaste y con qué medio?'; no nombres herramientas, parámetros, JSON ni códigos internos. "
    "No menciones proveedores de IA ni errores técnicos salvo que el usuario pida una explicación técnica. "
    "Conserva exactamente importes, fechas, cantidades e identificadores verificados; no inventes ni redondees datos. "
    "Distingue los totales del negocio de los pagos de una persona; no atribuyas a alguien pagos ajenos. "
    "Nunca inventes resultados ni afirmes que guardaste, enviaste dinero o completaste una acción sin confirmación del sistema. "
    "Ser cercano no cambia los permisos ni sustituye el botón Confirmar."
)


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
        return "Hasta luego. Aquí estaré cuando necesites consultar algo."
    return None
