"""Planificación laboral: lecturas acotadas y cambios siempre confirmados."""

from datetime import timedelta
import re

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, transaction
from django.db.models import Q
from django.utils import timezone

from mainApp.models import Empleado, Sucursal, TelegramAccionPendiente, TurnoEmpleado
from . import employee_schedule as schedule
from . import employee_rotation as rotation
from .telegram_search import resolve_name
from .telegram_wording import page_note, period_phrase


WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _day(value):
    return f"{WEEKDAYS[value.weekday()]} {value:%d/%m/%Y}"


def _hour(value):
    minutes = f":{value.minute:02}" if value.minute else ""
    return f"{value.hour % 12 or 12}{minutes} {'a. m.' if value.hour < 12 else 'p. m.'}"


def _hours(turn, *, vista="agenda"):
    begin, finish = schedule.parse_time(turn["inicio"]), schedule.parse_time(turn["fin"])
    if turn.get("descanso"):
        return "Descanso"
    if vista == "entrada":
        return f"Entrada: {_hour(begin)}"
    end_day = f" del {_day(finish)}" if begin.date() != finish.date() else ""
    if vista == "salida":
        return f"Salida: {_hour(finish)}{end_day}"
    return f"{_hour(begin)} a {_hour(finish)}{end_day}"


def _shift_text(turn):
    return f"{_day(schedule.parse_time(turn['inicio']))} · {_hours(turn)}"


SCHEDULE_PERIODS = r"hoy|ayer|manana|pasado manana|esta semana|la proxima semana|proxima semana|la semana que viene|semana que viene|la semana pasada"


def calendar_period(period):
    today = timezone.localdate(timezone=schedule.COLOMBIA)
    offsets = {"hoy": 0, "ayer": -1, "manana": 1, "pasado manana": 2}
    if period in offsets:
        day = today + timedelta(days=offsets[period])
        return day, day
    if period == "este mes":
        first = today.replace(day=1)
        return first, (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    if period == "el mes pasado":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    monday = today - timedelta(days=today.weekday())
    if period == "la semana pasada":
        monday -= timedelta(days=7)
    elif period in {"la proxima semana", "proxima semana", "la semana que viene", "semana que viene"}:
        monday += timedelta(days=7)
    elif period != "esta semana":
        raise ValueError("Período de calendario no reconocido")
    return monday, monday + timedelta(days=6)


def common_schedule_request(normalized):
    """Solo lecturas completas y conocidas. Las peticiones mixtas pasan a la IA."""
    text = re.sub(r"^(?:muestrame|dame|quiero ver|ver|cual es) ", "", normalized)
    suffix = r"(?: (?:(?:de|para) )?(?P<period>" + SCHEDULE_PERIODS + r"))?"
    personal = re.fullmatch(
        r"(?P<request>mi horario|mis horarios|mi calendario|mis turnos|cuando trabajo|que turno tengo|"
        r"cuando descanso|mis descansos|que dias descanso|a que hora entro|a que hora salgo)" + suffix, text,
    )
    team = re.fullmatch(
        r"(?P<request>horarios|horarios de todos|calendario del equipo|quien trabaja|quienes trabajan|quien descansa|quienes descansan)" + suffix, text,
    )
    named = re.fullmatch(
        r"(?:el )?(?:horario|calendario|los horarios) de (?P<employee>[a-zñ ]{2,80}?) (?:(?:de|para) )?(?P<period>" + SCHEDULE_PERIODS + r")", text,
    )
    match = personal or team or named
    if not match:
        return None
    args = {}
    request = match["request"] if not named else "horario"
    if named:
        name = named["employee"].strip()
        if re.search(r"\b(?:y|o|todos|equipo|hoy|ayer|manana|semana|cambia|mueve|cancela)\b", name):
            return None
        args["empleado"] = name
    if team:
        args["todos"] = True
    if "descans" in request:
        args["tipo"] = "descanso"
    elif request in {"quien trabaja", "quienes trabajan", "cuando trabajo"}:
        args["tipo"] = "trabajo"
    if request in {"a que hora entro", "a que hora salgo"}:
        args["vista"] = "entrada" if "entro" in request else "salida"
    period = match["period"] or ("hoy" if args.get("vista") or request.startswith("quien") else None)
    if period:
        start, end = calendar_period(period)
        args.update(desde=start.isoformat(), hasta=end.isoformat())
    return "consultar_horarios_empleados", args


def schedule_buttons(audit_id, arguments):
    start, end, _, _ = schedule.date_window(arguments)
    unit = "día" if start == end else "semana" if (end - start).days == 6 else "período"
    return [
        [{"text": f"← {unit.capitalize()} anterior", "callback_data": f"schedule:{audit_id}:anterior"},
         {"text": "Hoy", "callback_data": f"schedule:{audit_id}:hoy"},
         {"text": f"{unit.capitalize()} siguiente →", "callback_data": f"schedule:{audit_id}:siguiente"}],
        [{"text": "Ver turnos", "callback_data": f"schedule:{audit_id}:trabajo"},
         {"text": "Ver descansos", "callback_data": f"schedule:{audit_id}:descanso"},
         {"text": "Ver ambos", "callback_data": f"schedule:{audit_id}:todos"}],
    ]


def handle_schedule_callback(update, profile, client):
    from mainApp.models import TelegramAuditoria
    bot = _bot()
    match = re.fullmatch(r"schedule:([1-9][0-9]{0,17}):(anterior|siguiente|hoy|trabajo|descanso|todos)", str(update.texto or ""))
    audit = None
    if match and profile is not None and profile.activo and profile.usuario.is_active:
        audit = TelegramAuditoria.objects.filter(
            pk=int(match[1]), usuario=profile.usuario,
            telegram_user_id=profile.telegram_user_id, telegram_chat_id=profile.telegram_chat_id,
            accion="consultar_horarios_empleados", exitoso=True,
            creado_en__gte=timezone.now() - timedelta(hours=24),
        ).first()
    if audit is None:
        client.answer_callback(update.callback_query_id, "Consulta no disponible")
        return bot.BotReply("Ese calendario venció o no pertenece a tu cuenta. Pídeme el horario de nuevo.", "calendario_invalido")
    args = dict(audit.argumentos, pagina=1)
    action = match[2]
    if action in {"trabajo", "descanso", "todos"}:
        args.update(tipo=action, vista="agenda")
    else:
        start, end, _, _ = schedule.date_window(args)
        if action == "hoy":
            start = end = timezone.localdate(timezone=schedule.COLOMBIA)
        else:
            delta = timedelta(days=((end - start).days + 1) * (1 if action == "siguiente" else -1))
            start, end = start + delta, end + delta
        args.update(desde=start.isoformat(), hasta=end.isoformat())
    client.answer_callback(update.callback_query_id, "Consultando horario…")
    # La consulta original pertenece a esta cuenta/chat; se vuelven a comprobar
    # permisos y datos actuales. Los botones nunca preparan ni guardan cambios.
    return bot._execute_tool(profile, "consultar_horarios_empleados", args, update)


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
    return resolve_name(Empleado.objects.all(), name, ("nombre", "apellido"), entity="un empleado")


def _branch(raw):
    return resolve_name(Sucursal.objects.all(), raw, entity="una sucursal")


def tool_schedule(profile, arguments):
    bot = _bot()
    bot._require_access(profile, "mi_horario")
    user = profile.usuario
    try:
        args = dict(arguments)
        vista = args.get("vista", "agenda")
        if vista in {"entrada", "salida"} and not args.get("hasta"):
            args["hasta"] = args.get("desde") or timezone.localdate(timezone=schedule.COLOMBIA).isoformat()
        start, end, _, _ = schedule.date_window(args)
        kind = args.get("tipo", "trabajo" if vista in {"entrada", "salida"} else "todos")
        if vista not in {"agenda", "entrada", "salida"} or kind not in {"todos", "trabajo", "descanso"}:
            raise bot.TelegramBotError("Puedo mostrar la agenda, las entradas, las salidas o los descansos.")
        if vista != "agenda" and kind != "trabajo":
            raise bot.TelegramBotError("Los descansos no tienen hora de entrada o salida. Pide los turnos o los descansos por separado.")
        personal = not args.get("todos") and not args.get("empleado")
        employee = schedule.own_employee(user) if personal else None
        branch = None
        if args.get("todos"):
            schedule.require_access(user)
        if args.get("empleado"):
            employee = _employee(user, args["empleado"])
            args["empleado_id"] = employee.pk
            args["empleado"] = str(args["empleado_id"])
        if args.get("sucursal"):
            branch = _branch(args["sucursal"])
            args["sucursal_id"] = branch.pk
            args["sucursal"] = str(args["sucursal_id"])
        if personal and employee is None:
            if schedule.can_view_all(user):
                return bot.BotReply("Tu cuenta no tiene un empleado vinculado. ¿De quién quieres ver el horario? También puedes decir «horarios de todos».", "consultar_horarios_empleados")
            return bot.BotReply("Tu usuario no tiene una ficha de empleado vinculada. Pide al administrador que la vincule para consultar tu horario.", "consultar_horarios_empleados")
        rows = rotation.calendar_events(user, args, personal=personal)
        if vista == "salida" and (start.year, start.month, start.day) > (2000, 1, 1):
            # El calendario usa intervalos abiertos al final. Para consultar
            # salidas se incluyen también las que ocurren exactamente a las
            # 00:00 del primer día, sin aumentar el máximo de 93 días consultados.
            previous_day = (start - timedelta(days=1)).isoformat()
            boundary = rotation.calendar_events(user, dict(args, desde=previous_day, hasta=previous_day), personal=personal)
            rows = list({turn["id"]: turn for turn in [*boundary, *rows]}.values())
        if kind != "todos":
            rows = [turn for turn in rows if bool(turn.get("descanso")) == (kind == "descanso")]
        # Preguntar cuándo entra/sale se refiere al día de ese extremo, no al
        # día en que empezó un turno nocturno que también cruza el intervalo.
        if vista in {"entrada", "salida"}:
            field = "inicio" if vista == "entrada" else "fin"
            rows = [turn for turn in rows if start <= schedule.parse_time(turn[field]).date() <= end]
            rows.sort(key=lambda turn: (turn[field], str(turn["id"])))
        count = len(rows)
        pages = max(1, (count + bot.LIST_PAGE_SIZE - 1) // bot.LIST_PAGE_SIZE)
        page = schedule.positive_id(args.get("pagina", 1), "número de página")
        if page > pages:
            raise bot.TelegramBotError(f"La consulta tiene {pages} página(s).")
        subject = f"de {bot._list_text(str(employee), 100)}" if employee else "del equipo"
        title = {"entrada": "Entradas", "salida": "Salidas"}.get(vista, "Descansos" if kind == "descanso" else "Turnos" if kind == "trabajo" else "Horario")
        lines = [f"{title} {subject} · {period_phrase(start, end, timezone.localdate(timezone=schedule.COLOMBIA))} (Colombia)" + page_note(page, pages)]
        if branch:
            lines.append(f"Sucursal: {bot._list_text(branch.nombre, 80)}")
        last_day = None
        for turn in rows[(page - 1) * bot.LIST_PAGE_SIZE:page * bot.LIST_PAGE_SIZE]:
            begin, finish = schedule.parse_time(turn["inicio"]), schedule.parse_time(turn["fin"])
            day = finish if vista == "salida" else begin
            if day.date() != last_day:
                lines.append(f"\n{_day(day).capitalize()}")
                last_day = day.date()
            hours = f"Salida: {_hour(finish)}" if vista == "salida" else _hours(turn, vista=vista)
            name = "" if employee else f"{bot._list_text(turn['empleado'], 80)} · "
            location = f" · {bot._list_text(turn['sucursal'], 80)}" if not branch else ""
            lines.append(f"• {name}{hours}{location} · #{turn['id']}")
            if turn.get("excepcion"):
                lines.append("  Ajuste solo para esta fecha.")
            if args.get("detalle") and turn.get("rotacion_id"):
                lines.append(f"  Semana {turn['semana_rotacion']}/{turn['semanas_ciclo']} · {'ajustado solo para esta fecha' if turn['excepcion'] else 'horario que se repite'}")
            if args.get("detalle") and turn["notas"]:
                lines.append("  " + bot._list_text(turn["notas"], 160))
        if not count:
            if kind == "descanso":
                lines.append("No encontré descansos registrados en estas fechas. Eso no confirma que haya turno de trabajo.")
            else:
                lines.append("No encontré horarios registrados que coincidan. Eso no confirma un descanso.")
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
        verb = {"crear": "asignar", "editar": "cambiar", "cancelar": "cancelar"}[operation]
        lines = [f"¿Confirmas que quieres {verb} este horario?", f"Empleado: {bot._list_text(after['empleado'], 100)}", f"Sucursal: {bot._list_text(after['sucursal'], 100)}"]
        if before:
            lines.append(f"Turno #{before['id']} · horario actual: {_shift_text(before)}")
        lines.append(f"{'Horario a cancelar' if operation == 'cancelar' else 'Horario propuesto'}: {_shift_text(after)} (Colombia)")
        if after.get("notas"):
            lines.append("Notas: " + bot._list_text(after["notas"], 300))
        if payload.get("alcance"):
            lines.append("Alcance: SOLO ESTA FECHA." if payload["alcance"] == "fecha" else f"Alcance: ESTA FECHA Y LAS SIGUIENTES REPETICIONES de esta jornada, cada {after['semanas_ciclo']} semanas. Las otras jornadas y el historial anterior no cambian.")
        lines.append("Todavía no lo he guardado. Confirma en los próximos 10 minutos. No modifica ninguna caja.")
        pending = TelegramAccionPendiente.objects.create(
            telegram_usuario=profile, actualizacion=update, accion="turno_empleado",
            argumentos={"payload": payload, "anterior": before, "propuesto": after},
            resumen=f"{operation.capitalize()} horario de {after['empleado']}: {_shift_text(after)}"[:500],
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
        return f"Listo, el turno #{event['id']} quedó {'cancelado' if event['cancelado'] else 'guardado'}.{scope}"
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
    "sucursal": {"type": "STRING", "description": "ID o nombre de sucursal opcional. El servidor busca similitudes y pregunta ante ambigüedad."},
    "todos": {"type": "BOOLEAN", "description": "Solo si pide horarios de todos los empleados; exige permiso de calendario global."},
    "tipo": {"type": "STRING", "enum": ["todos", "trabajo", "descanso"], "description": "trabajo para quién trabaja, descanso para cuándo/quién descansa. Solo descansos explícitamente registrados: ausencia de turno NO es descanso. Por defecto todos, excepto entradas/salidas que usan trabajo."},
    "vista": {"type": "STRING", "enum": ["agenda", "entrada", "salida"], "description": "entrada/salida si pregunta solo a qué hora entra/sale; filtra por fecha de entrada/salida, incluso al cruzar medianoche. agenda para horario completo."},
    "detalle": {"type": "BOOLEAN", "description": "Solo si pide notas o detalles del ciclo/rotación; por defecto respuesta compacta."},
    "pagina": {"type": "INTEGER"},
}
TOOL_DEFINITIONS = [
    {"name": "consultar_horarios_empleados", "description": "Consulta el calendario laboral planificado: agenda, quién trabaja/descansa o a qué hora entra/sale. No confirma asistencia real ni consulta cajas. Por defecto MI horario de los próximos siete días; para entrada/salida sin fecha usa hoy. Otros empleados requieren permiso. Devuelve referencias de turno para editar o cancelar.", "parameters": {"type": "OBJECT", "properties": READ_PROPERTIES}},
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
