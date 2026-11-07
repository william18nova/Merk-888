"""Controles conversacionales del bot, sin confirmar operaciones desde la IA."""
import re

from django.core.exceptions import PermissionDenied


LINK_CODE = re.compile(r"\bNOVA-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8}\b", re.IGNORECASE)


def _bot():
    from . import telegram_bot
    return telegram_bot


def _active(profile):
    if not profile.activo or not profile.usuario.is_active:
        raise PermissionDenied("Usuario inactivo.")


def tool_help(profile, arguments):
    _active(profile)
    return _bot().HELP_TEXT


def tool_account(profile, arguments):
    _active(profile)
    role = getattr(getattr(profile.usuario, "rolid", None), "nombre", "Sin rol")
    return f"Vinculado como {profile.usuario.nombreusuario} · rol {role}."


def tool_proposal_buttons(profile, arguments, update=None):
    # Confirmar o descartar por voz no ejecuta nada: se recupera el teclado real.
    from .telegram_proposals import show_pending_buttons
    return show_pending_buttons(profile, update)


def link_request(text):
    """Solo una vinculación inequívoca; los códigos nunca se envían a la IA."""
    match = re.fullmatch(
        r"\s*(?:(?:hola|por favor)[, ]+)?(?:vincula(?:r|me)?|conecta(?:r)?|enlaza(?:r)?|asocia(?:r)?)\s+"
        r"(?:mi\s+)?cuenta\s+(?:con\s+)?(?:el\s+)?c[oó]digo\s+"
        r"(?P<code>NOVA-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{8})\s*[.!]?\s*", str(text), re.IGNORECASE,
    )
    return match["code"].upper() if match else None


def conversation_history(updates):
    """Solo mensajes operativos; jamás códigos de vínculo o eventos de botones."""
    history = []
    for item in reversed(updates):
        if getattr(item, 'tipo', '') == 'FOTO':
            # No reenviar fotos, identificadores de archivo ni sus captions a la IA de texto.
            if item.intencion == 'preparar_pago_comprobante':
                history.append({'role': 'user', 'text': 'Envié una foto para crear un borrador de comprobante.'})
                history.append({'role': 'model', 'text': 'Para completarlo o corregirlo usa completar_comprobante; aún requiere revisión y botón.'})
            continue
        text = str(item.transcripcion or item.texto or "").strip()
        if (item.intencion in {"vincular", "sin_vinculo"}
                or getattr(item, "tipo", "TEXTO") not in {"TEXTO", "VOZ"}
                or LINK_CODE.search(text) or text.lower().startswith("/vincular")):
            continue
        if text:
            history.append({"role": "user", "text": text})
        if item.respuesta:
            history.append({"role": "model", "text": item.respuesta})
    return history


TOOL_DEFINITIONS = [
    {"name": "consultar_ayuda", "description": "Explica cómo hablar o escribir al bot y qué comandos y funciones ofrece; equivalente a /ayuda o /start. No exige aprender comandos ni registra cambios.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "consultar_cuenta", "description": "Consulta a qué cuenta y rol de NovaPOS está vinculado este chat, equivalente a /estado. No muestra tokens, contraseñas ni claves especiales.",
     "parameters": {"type": "OBJECT", "properties": {}}},
    {"name": "recuperar_botones", "description": "Vuelve a mostrar la propuesta pendiente y sus botones reales para revisarla, confirmarla o descartarla. Usar si pide confirmar por voz/texto, no ve los botones o quiere descartar una solicitud aún pendiente. No confirma, cancela ni crea operaciones por sí sola; no anula pagos ya registrados ni turnos laborales.",
     "parameters": {"type": "OBJECT", "properties": {}}},
]
TOOL_FUNCTIONS = {"consultar_ayuda": tool_help, "consultar_cuenta": tool_account,
                  "recuperar_botones": tool_proposal_buttons}
