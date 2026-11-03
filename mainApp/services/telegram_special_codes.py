"""Claves especiales de Merk2 (merk2888), con la misma autorización de la web.

Nunca entrega claves a la IA, ni las guarda en propuestas, auditorías o historial.
Solo una cuenta vinculada Web Master puede preparar y confirmar su generación.
"""
import re
from datetime import timedelta
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from mainApp.permissions import is_web_master_role
from .special_discount import SpecialDiscountError, generate_one_time_code
from .telegram_ai_policy import SPECIAL_CODE_CLIENT_PATTERN, normalized


TOOL_DEFINITION = {
    "name": "preparar_clave_merk2",
    "description": "Prepara una clave de descuento especial Merk2/merk2888. Solo Web Master; se genera únicamente con el botón Confirmar. No es una contraseña de acceso.",
    "parameters": {"type": "OBJECT", "properties": {}},
}


def _bot():
    from . import telegram_bot
    return telegram_bot


def require_access(profile):
    user = profile.usuario
    if (not profile.activo or not getattr(user, "is_authenticated", False)
            or not user.is_active or not is_web_master_role(user)):
        raise PermissionDenied("Solo un Web Master vinculado puede generar claves especiales de Merk2 (merk2888).")


def direct_request(text):
    """Texto o transcripción completa: pedir una propuesta, nunca confirmarla.

    Admite peticiones breves y grafías de voz conocidas. El cliente siempre se
    muestra como Merk2 / merk2888 en los botones antes de generar una clave.
    """
    if not isinstance(text, str) or len(text) > 320:
        return False
    # La puntuación automática de la voz no cambia la intención. No eliminar
    # palabras, negaciones ni otros símbolos para rescatar parte de una orden.
    query = re.sub(r"\s+", " ", re.sub(r"[¿?¡!.,]", " ", normalized(text))).strip()
    return bool(re.fullmatch(
        r"(?:(?:hola|oye|por favor|porfa|nova|asistente|bot) )*"
        r"(?:(?:(?:me )?(?:puedes|podrias) )?"
        r"(?:genera|generar|generame|generarme|crear|crea|creame|crearme|dar|dame|darme|"
        r"regalar|regalame|regalarme|sacar|sacame|sacarme|enviar|enviame|enviarme|mandar|mandame|mandarme) "
        r"|me (?:generas?|creas?|das?|regalas?|sacas?|envias?|mandas?) "
        r"|(?:necesito|quiero|quisiera)(?: que (?:me )?(?:generes?|crees?|des?|regales?|saques?|envies?)"
        r"| (?:generar|crear|solicitar))? )?"
        r"(?:por favor |porfa )?"
        r"(?:(?:un|una|la|las|los|otra|otro|unas|unos) )?(?:claves?|codigos?)(?: especial(?:es)?)?(?: de descuento)? "
        r"(?:(?:de|del|para|en) (?:el )?)?" + SPECIAL_CODE_CLIENT_PATTERN +
        r"(?: (?:por favor|porfa|gracias))*",
        query,
    ))


def prepare_code(profile, arguments, update=None):
    from mainApp.models import TelegramAccionPendiente
    from .telegram_proposals import proposal_keyboard
    require_access(profile)
    if arguments:
        raise _bot().TelegramBotError("Esta operación no admite claves, usuarios, plazos ni permisos personalizados.")
    action = TelegramAccionPendiente.objects.create(
        telegram_usuario=profile, actualizacion=update, accion="generar_clave_merk2",
        argumentos={"solicitud_id": uuid4().hex}, resumen="Generar clave especial Merk2 / merk2888",
        vence_en=timezone.now() + timedelta(minutes=_bot().ACTION_TTL_MINUTES),
    )
    return _bot().BotReply(
        "Generar una clave de descuento especial para Merk2 (cliente merk2888).\n"
        "Se genera una sola clave a la vez. "
        "Será de un solo uso y vencerá 15 minutos después de generarla. "
        "No cambia contraseñas ni concede permisos. Si ya hay una clave vigente, no la reemplazaré.\n\n"
        "Pulsa Generar clave para confirmarlo; aún no se ha generado ninguna.",
        "preparar_clave_merk2", proposal_id=str(action.pk), reply_markup=proposal_keyboard(action),
    )


def confirm_code(profile, action):
    """Se llama dentro del bloqueo transaccional de la propuesta del bot."""
    require_access(profile)
    if action.telegram_usuario_id != profile.pk or action.accion != "generar_clave_merk2":
        raise PermissionDenied("La propuesta no pertenece a tu cuenta.")
    if action.estado != "PENDIENTE" or action.vence_en <= timezone.now():
        raise _bot().TelegramBotError("La propuesta ya se resolvió o venció; no se generó otra clave.")
    try:
        authorization, code = generate_one_time_code(profile.usuario, action.argumentos.get("solicitud_id"))
    except SpecialDiscountError as exc:
        action.estado = "ERROR"
        action.resuelto_en = timezone.now()
        action.save(update_fields=["estado", "resuelto_en"])
        _bot()._audit(profile, "confirmar_clave_merk2", {"accion_id": str(action.pk)},
                      successful=False, detail=str(exc))
        return _bot().BotReply(f"No se generó una nueva clave: {exc}", "clave_merk2_no_generada")
    action.estado = "CONFIRMADA"
    action.resuelto_en = timezone.now()
    action.save(update_fields=["estado", "resuelto_en"])
    reference = authorization.referencia
    _bot()._audit(profile, "confirmar_clave_merk2", {"accion_id": str(action.pk)}, detail=f"Referencia {reference}")
    deadline = timezone.localtime(authorization.expira_en).strftime("%d/%m/%Y %H:%M")
    return _bot().BotReply(
        f"Clave especial Merk2 (merk2888) generada.\nReferencia: {reference}\n"
        f"Un solo uso. Vence el {deadline} (Colombia).\n"
        "La clave va sola en el siguiente mensaje para copiarla fácilmente. "
        "No la compartas fuera del proceso autorizado. Solo se envía una vez; "
        "si no recibiste la clave, revisa/revócala en la web antes de solicitar otra.",
        "clave_merk2_generada",
        history_text=f"Se generó una clave especial Merk2. Referencia: {reference}. La clave se omitió del historial.",
        copyable_code=code,
    )
