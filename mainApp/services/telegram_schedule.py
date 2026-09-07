"""Planificación laboral: lecturas acotadas y cambios siempre confirmados."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from mainApp.models import Empleado, Sucursal, TelegramAccionPendiente, TurnoEmpleado
from . import employee_schedule as schedule
from . import employee_rotation as rotation


def _bot():
    from . import telegram_bot
    return telegram_bot


def _employee(user, raw):
    bot = _bot()
    name = str(raw or "").strip()
    own = schedule.own_employee(user)
    if bot._normalized_text(name) in {"yo", "mi", "mio"}:
        if not own:
            raise bot.TelegramBotError("Tu usuario no tiene una ficha de empleado vinculada.")
        return own
    if not schedule.can_view_all(user):
        if own and (name == str(own.pk) or bot._normalized_text(name) == bot._normalized_text(str(own))):
            return own
        raise PermissionDenied("Solo puedes consultar tu propio horario.")
    rows = Empleado.objects.all()
    if name.isascii() and name.isdigit():
        rows = rows.filter(pk=schedule.positive_id(name, "ID de empleado"))
    else:
        if not name:
            raise bot.TelegramBotError("Indica el nombre o ID del empleado.")
        for word in name.split():
            rows = rows.filter(Q(nombre__icontains=word) | Q(apellido__icontains=word))
    matches = list(rows[:2])
    if len(matches) != 1:
        raise bot.TelegramBotError("No encontré un empleado único con ese nombre. Consulta /empleados e indica su ID.")
    return matches[0]


def _branch(raw):
    name = str(raw or "").strip()
    rows = Sucursal.objects.filter(pk=schedule.positive_id(name, "ID de sucursal")) if name.isascii() and name.isdigit() else Sucursal.objects.filter(nombre__iexact=name)
    matches = list(rows[:2])
    if len(matches) != 1:
        raise _bot().TelegramBotError("Indica el ID o nombre exacto de una sucursal existente.")
    return matches[0]


def tool_schedule(profile, arguments):
    bot = _bot()
    bot._require_access(profile, "mi_horario")
    user = profile.usuario
    try:
        args = dict(arguments)
        start, end, _, _ = schedule.date_window(args)
        personal = not args.get("todos") and not args.get("empleado")
        if args.get("todos"):
            schedule.require_access(user)
        if args.get("empleado"):
            args["empleado_id"] = _employee(user, args["empleado"]).pk
            args["empleado"] = str(args["empleado_id"])
        if args.get("sucursal"):
            args["sucursal_id"] = _branch(args["sucursal"]).pk
            args["sucursal"] = str(args["sucursal_id"])
        if personal and schedule.own_employee(user) is None:
            return bot.BotReply("Tu usuario no tiene una ficha de empleado vinculada. Pide al administrador que la vincule para consultar tu horario.", "consultar_horarios_empleados")
        rows = rotation.calendar_events(user, args, personal=personal)
        count = len(rows)
        pages = max(1, (count + bot.LIST_PAGE_SIZE - 1) // bot.LIST_PAGE_SIZE)
        page = schedule.positive_id(args.get("pagina", 1), "número de página")
        if page > pages:
            raise bot.TelegramBotError(f"La consulta tiene {pages} página(s).")
        lines = [f"{'Mi horario' if personal else 'Horarios de empleados'} · {start:%d/%m/%Y} – {end:%d/%m/%Y}", f"{count} turno(s) · página {page}/{pages} · hora Colombia"]
        for turn in rows[(page - 1) * bot.LIST_PAGE_SIZE:page * bot.LIST_PAGE_SIZE]:
            begin, finish = schedule.parse_time(turn["inicio"]), schedule.parse_time(turn["fin"])
            hours = "Descanso · día completo" if turn.get("descanso") else f"{begin:%d/%m %H:%M} → {finish:%d/%m %H:%M}"
            lines.append(f"• Turno #{turn['id']} · {bot._list_text(turn['empleado'], 80)}\n  {begin:%d/%m} · {hours} · {bot._list_text(turn['sucursal'], 80)}")
            if turn.get("rotacion_id"):
                lines.append(f"  Semana {turn['semana_rotacion']}/{turn['semanas_ciclo']} · {'excepción puntual' if turn['excepcion'] else 'rotación'}")
            if turn["notas"]:
                lines.append("  " + bot._list_text(turn["notas"], 160))
        if not count:
            lines.append("No hay jornadas programadas en este intervalo.")
        canonical = {key: value for key, value in args.items() if key in READ_PROPERTIES}
        canonical.update(desde=start.isoformat(), hasta=end.isoformat(), pagina=page)
        return bot.BotReply("\n".join(lines), "consultar_horarios_empleados", pagination={"page": page, "pages": pages, "arguments": canonical})
    except schedule.ScheduleError as exc:
        raise bot.TelegramBotError(str(exc)) from None


def tool_prepare_schedule(profile, arguments, update=None):
    bot = _bot()
    user = profile.usuario
    schedule.require_access(user, write=True)
    try:
        operation = arguments.get("operacion")
        payload = {"operacion": operation}
        if operation != "crear":
            if arguments.get("turno_referencia"):
                if arguments.get("turno_id") is not None:
                    raise bot.TelegramBotError("Usa solo turno_id o turno_referencia, no ambos.")
                _, _, _, turn = rotation.occurrence(arguments["turno_referencia"])
                payload.update(id=turn["id"], version=turn["version"], alcance=arguments.get("alcance"))
                rotation._scope(payload)
            else:
                if arguments.get("alcance") not in {None, "fecha"} or "descanso" in arguments:
                    raise bot.TelegramBotError("Ese turno es independiente; para cambiar la rotación indica su referencia r… y el alcance.")
                turn = TurnoEmpleado.objects.filter(pk=schedule.positive_id(arguments.get("turno_id"), "ID de turno")).first()
                if turn is None:
                    raise bot.TelegramBotError("No encontré ese turno de empleado.")
                payload.update(id=turn.pk, version=turn.version)
        elif arguments.get("turno_id") is not None or arguments.get("turno_referencia") is not None:
            raise bot.TelegramBotError("No indiques ID de turno al crear una jornada.")
        elif arguments.get("alcance") not in {None, "fecha"} or "descanso" in arguments:
            raise bot.TelegramBotError("Crear desde el bot asigna una jornada independiente; para modificar el ciclo usa una referencia de la rotación.")
        if operation == "cancelar" and set(arguments) - {"operacion", "turno_id", "turno_referencia", "alcance"}:
            raise bot.TelegramBotError("Para cancelar indica solo el ID del turno; no cambios de horario.")
        if operation == "editar" and not set(arguments) & {"empleado", "sucursal", "inicio", "fin", "notas", "descanso"}:
            raise bot.TelegramBotError("Indica qué fecha, hora, empleado, sucursal o nota quieres modificar.")
        if "empleado" in arguments or operation == "crear":
            employee = _employee(user, arguments.get("empleado"))
            payload["empleado_id"] = employee.pk
            if operation == "crear" and "sucursal" not in arguments:
                payload["sucursal_id"] = employee.sucursalid_id
        if "sucursal" in arguments:
            payload["sucursal_id"] = _branch(arguments["sucursal"]).pk
        for field in ("inicio", "fin", "notas", "descanso"):
            if field in arguments:
                payload[field] = arguments[field]
        before, after = schedule.preview_change(user, payload)
        lines = [f"Propuesta: {operation} turno de empleado", f"Empleado: {bot._list_text(after['empleado'], 100)}", f"Sucursal: {bot._list_text(after['sucursal'], 100)}"]
        if before:
            lines.append(f"Turno #{before['id']} · horario actual: {before['inicio'][:16]} → {before['fin'][:16]}")
        lines.append(f"{'Horario a cancelar' if operation == 'cancelar' else 'Horario propuesto'}: {after['inicio'][:16]} → {after['fin'][:16]} (Colombia)")
        if after.get("notas"):
            lines.append("Notas: " + bot._list_text(after["notas"], 300))
        if payload.get("alcance"):
            lines.append("Alcance: SOLO ESTA FECHA." if payload["alcance"] == "fecha" else f"Alcance: ESTA FECHA Y LAS SIGUIENTES REPETICIONES de esta jornada, cada {after['semanas_ciclo']} semanas. Las otras jornadas y el historial anterior no cambian.")
        lines.append("Todavía no se ha guardado. Confirma con el botón; vence en 10 minutos. Esto no abre ni modifica una caja.")
        pending = TelegramAccionPendiente.objects.create(
            telegram_usuario=profile, actualizacion=update, accion="turno_empleado",
            argumentos={"payload": payload, "anterior": before, "propuesto": after},
            resumen=f"{operation.capitalize()} horario de {after['empleado']}: {after['inicio'][:16]} → {after['fin'][:16]}"[:500],
            vence_en=timezone.now() + timedelta(minutes=bot.ACTION_TTL_MINUTES),
        )
        return bot.BotReply("\n".join(lines), "preparar_turno_empleado", reply_markup={"inline_keyboard": [[
            {"text": "Confirmar horario" if operation != "cancelar" else "Confirmar cancelación", "callback_data": f"confirm:{pending.pk}"},
            {"text": "Descartar propuesta", "callback_data": f"cancel:{pending.pk}"},
        ]]})
    except schedule.ScheduleError as exc:
        raise bot.TelegramBotError(str(exc)) from None


def confirm_schedule(profile, action):
    bot = _bot()
    schedule.require_access(profile.usuario, write=True)
    try:
        with transaction.atomic():
            payload = dict(action.argumentos["payload"], solicitud_id=str(action.pk))
            turn, _ = schedule.save_change(profile.usuario, payload, source="TELEGRAM")
            event = schedule.serialize(turn)
            action.estado = "CONFIRMADA"
            action.resuelto_en = timezone.now()
            action.save(update_fields=["estado", "resuelto_en"])
            bot._audit(profile, "confirmar_turno_empleado", action.argumentos, detail=f"Turno de empleado #{event['id']}")
        scope = " Se actualizó esta fecha y sus siguientes repeticiones." if payload.get("alcance") == "futuro" else ""
        return f"Turno de empleado #{event['id']} {'cancelado' if event['cancelado'] else 'guardado'} correctamente.{scope} Ya se refleja en el calendario y en Mi horario. No se modificó ninguna caja."
    except (schedule.ScheduleError, DatabaseError) as exc:
        message = str(exc) if isinstance(exc, schedule.ScheduleError) else "No fue posible guardar el horario. Revisa el calendario y solicita una nueva propuesta."
        action.estado = "ERROR"
        action.resuelto_en = timezone.now()
        action.save(update_fields=["estado", "resuelto_en"])
        bot._audit(profile, "confirmar_turno_empleado", action.argumentos, successful=False, detail=message)
        return "No se cambió el horario. " + message


READ_PROPERTIES = {
    "desde": {"type": "STRING", "description": "Fecha inicial YYYY-MM-DD. Por defecto hoy."},
    "hasta": {"type": "STRING", "description": "Fecha final inclusiva. Por defecto seis días después de desde. Máximo 93 días."},
    "empleado": {"type": "STRING", "description": "ID o nombre único del empleado; omitir para mi horario. No inventar IDs."},
    "sucursal": {"type": "STRING", "description": "ID o nombre exacto de sucursal opcional."},
    "todos": {"type": "BOOLEAN", "description": "Solo si pide horarios de todos los empleados; exige permiso de calendario global."},
    "pagina": {"type": "INTEGER"},
}
TOOL_DEFINITIONS = [
    {"name": "consultar_horarios_empleados", "description": "Consulta jornadas laborales PLANIFICADAS, no turnos financieros de caja. Por defecto consulta MI horario de los próximos siete días. Otros empleados requieren permiso. Devuelve IDs de turno para editar o cancelar.", "parameters": {"type": "OBJECT", "properties": READ_PROPERTIES}},
    {"name": "preparar_turno_empleado", "description": "Propone crear, editar o cancelar una jornada laboral. Solo guarda al pulsar Confirmar. Usa turno_id para jornadas independientes o turno_referencia para una jornada de rotación; en rotaciones exige alcance explícito, fecha o futuro, para esa misma jornada. No asume horas ni repeticiones; solicita datos faltantes. Crear usa por defecto la sucursal de la ficha del empleado y la muestra antes de confirmar. No opera turnos de caja.", "parameters": {"type": "OBJECT", "properties": {
        "operacion": {"type": "STRING", "enum": ["crear", "editar", "cancelar"]},
        "turno_id": {"type": "INTEGER", "description": "Solo para editar o cancelar jornadas independientes: ID real de la consulta de horarios. Para jornadas de rotación usa turno_referencia EN LUGAR de este campo; nunca envíes ambos."},
        "turno_referencia": {"type": "STRING", "description": "Para turnos de rotación, referencia completa rID-CLAVE-AAAAMMDD obtenida de consultar_horarios_empleados. Alternativa a turno_id."},
        "alcance": {"type": "STRING", "enum": ["fecha", "futuro"], "description": "OBLIGATORIO en rotaciones: fecha=solo esta ocurrencia; futuro=esta y siguientes repeticiones de la misma jornada. Si no lo dice explícitamente, PREGUNTAR, no asumir."},
        "descanso": {"type": "BOOLEAN", "description": "Solo para editar una ocurrencia de rotación. true exige inicio 00:00 y fin 00:00 del día siguiente; false la convierte en jornada de trabajo con horas explícitas."},
        "empleado": {"type": "STRING", "description": "ID o nombre único. Obligatorio al crear."},
        "sucursal": {"type": "STRING"},
        "inicio": {"type": "STRING", "description": "Fecha y hora explícitas YYYY-MM-DDTHH:MM en Colombia; obligatorio al crear."},
        "fin": {"type": "STRING", "description": "Fecha y hora final YYYY-MM-DDTHH:MM; obligatorio al crear. Turnos nocturnos terminan en la fecha siguiente. Máximo 24h."},
        "notas": {"type": "STRING", "description": "Solo notas indicadas por el usuario, máximo 300 caracteres."},
    }, "required": ["operacion"]}},
]
TOOL_FUNCTIONS = {"consultar_horarios_empleados": tool_schedule, "preparar_turno_empleado": tool_prepare_schedule}
