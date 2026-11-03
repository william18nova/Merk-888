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
from .telegram_ai_policy import normalized


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
    """Solo peticiones completas e inequívocas; no negaciones ni órdenes compuestas."""
    query = re.sub(r"\s+", " ", normalized(text)).strip(" ¿?¡!.,")
    return bool(re.fullmatch(
        r"(?:(?:hola|oye|por favor)[, ]+)*"
        r"(?:(?:(?:me )?(?:puedes|podrias) )?(?:genera|generar|generame|crear|crea|creame|dame|darme)"
        r"|(?:necesito|quiero)(?: que me generes| que generes)?) "
        r"(?:(?:un|una|la|otra) )?(?:clave|codigo)(?: especial| de descuento)? "
        r"(?:(?:de|del|para|en) )?(?:merk\s*(?:2|dos|2888))"
        r"(?:[, ]+(?:por favor|gracias))?",
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
        f"Clave especial Merk2 (merk2888): {code}\nReferencia: {reference}\n"
        f"Un solo uso. Vence el {deadline} (Colombia).\n"
        "No la compartas fuera del proceso autorizado. Solo se muestra una vez; "
        "si no recibiste la clave, revisa/revócala en la web antes de solicitar otra.",
        "clave_merk2_generada",
        history_text=f"Se generó una clave especial Merk2. Referencia: {reference}. La clave se omitió del historial.",
    )
