"""Recuperación de botones desde propuestas reales, nunca desde texto de la IA."""
import hashlib
import json
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.utils import timezone

PRESENTATION_KEY = "_telegram_confirmation"
ACTION_UI = {
    "registrar_pago": ("preparar_registro_pago", "Confirmar"),
    "editar_pago": ("preparar_edicion_pago", "Confirmar corrección"),
    "devolver_venta": ("preparar_devolucion_venta", "Confirmar devolución"),
    "turno_empleado": ("preparar_turno_empleado", "Confirmar horario"),
    "cambio_catalogo": ("preparar_cambio_catalogo", "Confirmar cambio"),
}
PROPOSAL_INTENTS = {value[0] for value in ACTION_UI.values()} | {"seleccionar_concepto_pago"}


def _bot():
    from . import telegram_bot
    return telegram_bot


def _fingerprint(arguments):
    data = {key: value for key, value in arguments.items() if key != PRESENTATION_KEY}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def proposal_keyboard(action):
    """Teclado obligatorio calculado únicamente con el estado del dominio."""
    if action.accion == "registrar_pago" and action.argumentos.get("concepto_eleccion_pendiente"):
        concept = action.argumentos["concepto"]
        rows = [[{"text": f"Usar {index + 1}: {option['nombre'][:48]}",
                  "callback_data": f"concept:{action.pk}:{index}"}]
                for index, option in enumerate(action.argumentos["concepto_opciones"])]
        rows.extend([
            [{"text": f"➕ Crear nuevo: {concept[:40]}", "callback_data": f"newconcept:{action.pk}"}],
            [{"text": "❌ Cancelar", "callback_data": f"cancel:{action.pk}"}],
        ])
        return {"inline_keyboard": rows}
    label = ACTION_UI[action.accion][1]
    if action.accion == "turno_empleado" and action.argumentos.get("payload", {}).get("operacion") == "cancelar":
        label = "Confirmar cancelación"
    return {"inline_keyboard": [[
        {"text": label, "callback_data": f"confirm:{action.pk}"},
        {"text": "Cancelar", "callback_data": f"cancel:{action.pk}"},
    ]]}


def _reply_action(profile, reply):
    from mainApp.models import TelegramAccionPendiente
    bot = _bot()
    try:
        action_id = UUID(str(reply.proposal_id))
    except (ValueError, TypeError, AttributeError):
        raise bot.TelegramBotError("No pude preparar una confirmación válida. No se guardó ningún cambio.") from None
    action = TelegramAccionPendiente.objects.filter(pk=action_id, telegram_usuario=profile).first()
    allowed_intents = {ACTION_UI[action.accion][0]} if action and action.accion in ACTION_UI else set()
    if action and action.accion == "registrar_pago":
        allowed_intents.add("seleccionar_concepto_pago")
    if action is None or reply.intent not in allowed_intents:
        raise bot.TelegramBotError("No encontré una propuesta válida para esos botones. No se guardó ningún cambio.")
    return action


def remember_confirmation(profile, reply):
    """Conservar detalle e identidad aunque se omita accidentalmente el teclado."""
    bot = _bot()
    if not isinstance(reply, bot.BotReply) or reply.intent not in PROPOSAL_INTENTS:
        return
    action = _reply_action(profile, reply)
    if action.estado != "PENDIENTE" or not reply.text.strip():
        raise bot.TelegramBotError("La propuesta ya no está disponible para confirmar.")
    # Los pagos se reconstruyen desde sus valores y su elección actual de concepto.
    if action.accion == "registrar_pago":
        return
    action.argumentos = dict(action.argumentos, **{PRESENTATION_KEY: {
        "version": 1, "texto": reply.text, "fingerprint": _fingerprint(action.argumentos),
    }})
    action.save(update_fields=["argumentos"])


def _require_action_access(profile, action):
    bot = _bot()
    if not profile.activo or not profile.usuario.is_active or action.telegram_usuario_id != profile.pk:
        raise PermissionDenied("La propuesta no pertenece a tu cuenta activa.")
    if action.accion == "registrar_pago":
        bot._require_access(profile, "registrar_egreso")
    elif action.accion == "editar_pago":
        bot._require_access(profile, "editar_egreso")
    elif action.accion == "devolver_venta":
        from .telegram_returns import _require_permission
        _require_permission(profile)
    elif action.accion == "turno_empleado":
        from .employee_schedule import require_access
        require_access(profile.usuario, write=True)
    elif action.accion == "cambio_catalogo":
        from .telegram_operations import _catalog_spec
        _catalog_spec(profile, action.argumentos.get("entidad"), action.argumentos.get("operacion"))
    else:
        raise bot.TelegramBotError("Esta propuesta ya no se puede recuperar. Solicita el cambio nuevamente.")


def restore_confirmation(profile, action, *, recovered=True):
    bot = _bot()
    _require_action_access(profile, action)
    if action.estado != "PENDIENTE" or action.vence_en <= timezone.now():
        return bot.BotReply("Esta propuesta ya se resolvió o venció. Pídeme el cambio de nuevo para revisar los datos actuales.", "propuesta_no_disponible")
    if action.accion == "registrar_pago":
        return (bot._expense_concept_choice_reply(action) if action.argumentos.get("concepto_eleccion_pendiente")
                else bot._expense_confirmation_reply(action))
    saved = action.argumentos.get(PRESENTATION_KEY)
    if (not isinstance(saved, dict) or saved.get("version") != 1
            or saved.get("fingerprint") != _fingerprint(action.argumentos)
            or not isinstance(saved.get("texto"), str) or not saved["texto"].strip()):
        # No dar un botón Confirmar junto a un resumen de 500 caracteres que
        # podría omitir cantidades, cambios, condiciones o alcance de rotación.
        return bot.BotReply("No tengo el detalle completo de esta propuesta antigua. Cancélala y pide el cambio de nuevo para revisarlo antes de confirmar.", "propuesta_sin_detalle", reply_markup={"inline_keyboard": [[
            {"text": "Cancelar esta propuesta", "callback_data": f"cancel:{action.pk}"},
        ]]})
    intent = ACTION_UI[action.accion][0]
    deadline = timezone.localtime(action.vence_en).strftime("%d/%m/%Y %H:%M")
    heading = "Propuesta original:\n" if recovered else ""
    return bot.BotReply(
        f"{heading}{saved['texto']}\n\nVencimiento original: {deadline}. Volver a mostrarla no amplía ese plazo.",
        intent, proposal_id=str(action.pk), reply_markup=proposal_keyboard(action),
    )


def ensure_proposal_buttons(update, reply):
    """Barrera final: una propuesta sale con su teclado real, nunca uno inferido."""
    bot = _bot()
    if not reply.proposal_id and reply.intent not in PROPOSAL_INTENTS:
        return reply
    profile = bot._profile_for_update(update)
    if profile is None:
        raise PermissionDenied("Vincula tu cuenta antes de confirmar una propuesta.")
    action = _reply_action(profile, reply)
    # No reutilizar texto/teclados de otra propuesta ni responder con un resumen.
    # El estado actual decide entre elegir concepto, confirmar o indicar vencimiento.
    canonical = restore_confirmation(profile, action, recovered=False)
    if canonical.intent in PROPOSAL_INTENTS:
        # No depender ni siquiera del teclado que haya devuelto el generador de
        # texto: se regenera siempre, incluida la elección obligatoria de concepto.
        canonical.proposal_id = str(action.pk)
        canonical.reply_markup = proposal_keyboard(action)
    return canonical


def reuse_update_proposal(update):
    """Reintentar el envío de la MISMA propuesta si el trabajador se interrumpió."""
    from mainApp.models import TelegramAccionPendiente
    bot = _bot()
    if update.tipo not in {"TEXTO", "VOZ"} or update.intentos < 2:
        return None
    actions = list(TelegramAccionPendiente.objects.filter(actualizacion_id=update.pk).order_by("creado_en", "pk")[:2])
    if not actions:
        return None
    profile = bot._profile_for_update(update)
    if profile is None or any(action.telegram_usuario_id != profile.pk for action in actions):
        raise PermissionDenied("La propuesta no pertenece a tu cuenta vinculada.")
    if len(actions) > 1:
        # Compatibilidad con duplicados previos; no elegir ni ejecutar uno al azar.
        return show_pending_buttons(profile, update)
    return restore_confirmation(profile, actions[0])


def show_pending_buttons(profile, update=None):
    from mainApp.models import TelegramAccionPendiente
    bot = _bot()
    if not profile.activo or not profile.usuario.is_active:
        raise PermissionDenied("Usuario inactivo.")
    pending = list(TelegramAccionPendiente.objects.filter(
        telegram_usuario=profile, estado="PENDIENTE", vence_en__gt=timezone.now(),
    ).order_by("creado_en", "pk")[:2])
    if not pending:
        return bot.BotReply(
            "No tienes una propuesta pendiente para confirmar. Si antes te pedí confirmar sin mostrar botones, "
            "envíame de nuevo la solicitud completa para prepararla. Decir «confirmar» no guarda cambios.",
            "sin_propuesta_pendiente",
        )
    if len(pending) == 1:
        return restore_confirmation(profile, pending[0])
    return bot._execute_tool(profile, "consultar_pendientes", {}, update)
