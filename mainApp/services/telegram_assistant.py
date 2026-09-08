"""Consultas compuestas y contexto acotado; nunca ejecuta código o SQL de la IA."""

import json
import re
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from .telegram_search import resolve_name
from .telegram_queries import QUERY_DEFINITION, SOURCES
from .telegram_wording import page_note, period_phrase


def _bot():
    from . import telegram_bot
    return telegram_bot


READ_TOOLS = (
    "consultar_datos",
    "consultar_ventas", "buscar_producto", "consultar_inventario", "consultar_pagos",
    "listar_empleados", "consultar_balance", "consultar_turnos", "consultar_registros",
    "consultar_detalle_operativo", "ranking_productos", "buscar_vistas",
    "consultar_informe", "consultar_resumen_negocio", "consultar_pendientes", "consultar_horarios_empleados",
)

DATE_TOOLS = {"consultar_ventas", "consultar_pagos", "consultar_balance", "ranking_productos", "consultar_informe", "consultar_resumen_negocio"}

GROUPS = {
    "ventas": {
        "empleado": ("empleadoid", "empleadoid__nombre", "empleadoid__apellido"),
        "cliente": ("clienteid", "clienteid__nombre", "clienteid__apellido"),
        "sucursal": ("sucursalid", "sucursalid__nombre"),
        "punto_pago": ("puntopagoid", "puntopagoid__nombre"),
        "dia": ("fecha",),
    },
    "pagos": {
        "concepto": ("concepto_id", "concepto__nombre"),
        "usuario": ("registrado_por_id", "registrado_por_nombre"),
        "dia": ("dia_local",),
    },
}


def _employee(raw):
    from mainApp.models import Empleado
    bot = _bot()
    return resolve_name(Empleado.objects.all(), raw, ("nombre", "apellido"), entity="un empleado")


def _report_rows(profile, args):
    from mainApp.models import Egreso, Venta
    bot = _bot()
    source = args["fuente"]
    if source == "ventas":
        bot._require_access(profile, "metricas_negocio", "ventas_diarias")
        rows, date_field, amount = Venta.objects.all(), "fecha", "total"
        if args.get("concepto") or args.get("usuario") or args.get("medio_pago") or "monto_min" in args or "monto_max" in args:
            raise bot.TelegramBotError("Concepto, usuario que registra, límites de monto y medio de pago filtran pagos. Para medios de ventas usa /ventas.")
        if args.get("sucursal"):
            branch = bot._find_branch(args["sucursal"])
            args["sucursal"] = str(branch.pk)
            rows = rows.filter(sucursalid=branch)
        if args.get("empleado"):
            employee = _employee(args["empleado"])
            args["empleado"] = str(employee.pk)
            rows = rows.filter(empleadoid=employee)
    else:
        bot._require_access(profile, "registrar_egreso")
        rows, date_field, amount = Egreso.objects.all(), "creado_en__date", "monto"
        if args.get("sucursal") or args.get("empleado"):
            raise bot.TelegramBotError("Los pagos operativos no están asociados a sucursales ni empleados; puedes filtrar por usuario que los registró.")
        rows, concept, user = bot._filter_expense_names(rows, args.get("concepto", ""), args.get("usuario", ""))
        if args.get("concepto"):
            args["concepto"] = concept
        if args.get("usuario"):
            args["usuario"] = user
        if args.get("medio_pago"):
            method = bot.normalize_payment_method_code(args["medio_pago"])
            codes = rows.order_by().values_list("medio_pago", flat=True).distinct()
            rows = rows.filter(medio_pago__in=[code for code in codes if bot.normalize_payment_method_code(code) == method])
        minimum = bot._expense_amount_filter(args["monto_min"]) if "monto_min" in args else None
        maximum = bot._expense_amount_filter(args["monto_max"]) if "monto_max" in args else None
        if minimum is not None and maximum is not None and minimum > maximum:
            raise bot.TelegramBotError("El monto mínimo no puede ser mayor que el máximo.")
        if minimum is not None:
            rows = rows.filter(monto__gte=minimum)
        if maximum is not None:
            rows = rows.filter(monto__lte=maximum)
    return rows, date_field, amount


def tool_report(profile, arguments):
    bot = _bot()
    args = dict(arguments)
    source, group = args["fuente"], args.get("agrupar", "total")
    limit = args.get("primeros")
    if limit is not None and not 1 <= limit <= 50:
        raise bot.TelegramBotError("Solicita entre 1 y 50 primeros resultados.")
    if group != "total" and group not in GROUPS[source]:
        raise bot.TelegramBotError("Esa agrupación no corresponde a la consulta. Ventas: empleado, cliente, sucursal, punto_pago o dia. Pagos: concepto, usuario o dia; por medios usa consultar_pagos.")
    start, end = bot._date_range(args)
    args.update(desde=start.isoformat(), hasta=end.isoformat())
    base, date_field, amount = _report_rows(profile, args)
    rows = base.filter(**{date_field + "__range": (start, end)})
    totals = rows.aggregate(importe=Sum(amount), cantidad=Count("pk"), promedio=Avg(amount))
    total, count = totals["importe"] or Decimal(0), totals["cantidad"]
    lines = [f"{source.capitalize()} {period_phrase(start, end, timezone.localdate())}"]
    filter_names = {"sucursal": "Sucursal", "empleado": "Empleado", "concepto": "Concepto", "usuario": "Registró", "medio_pago": "Medio", "monto_min": "Desde", "monto_max": "Hasta"}
    filters = []
    for key, label in filter_names.items():
        if key not in args:
            continue
        value = args[key]
        if key == "medio_pago":
            value = bot.payment_method_label(value)
        elif key in {"monto_min", "monto_max"}:
            value = bot._list_money(value)
        filters.append(f"{label}: {bot._list_text(value, 80)}")
    if filters:
        lines.append("Filtros: " + " · ".join(filters))
    show_count = args.get("incluir_cantidad") or args.get("orden") == "cantidad"
    show_average = args.get("incluir_promedio") or args.get("orden") == "promedio"
    if group == "total" or args.get("incluir_total") or args.get("comparar_anterior"):
        lines.append(f"Total: {bot._list_money(total)}")
        if show_count:
            lines.append(f"{count} {source if count != 1 else ('venta' if source == 'ventas' else 'pago')}")
        if show_average:
            lines.append(f"Promedio: {bot._list_money(totals['promedio']) if count else 'Sin datos'}")
    if args.get("comparar_anterior"):
        previous_end = start - timedelta(days=1)
        previous_start = start - timedelta(days=(end - start).days + 1)
        previous = base.filter(**{date_field + "__range": (previous_start, previous_end)}).aggregate(total=Sum(amount))["total"] or Decimal(0)
        difference = total - previous
        percent = f"{(difference / previous * 100):+.2f}%" if previous else "sin base para porcentaje: antes no hubo importe registrado"
        lines.append(f"Comparación del total con {previous_start:%d/%m/%Y} al {previous_end:%d/%m/%Y}: {bot._list_money(previous)}; diferencia {bot._list_money(difference)} · {percent}.")
    if source == "ventas":
        lines.append("Ventas con cambios y devoluciones ya reflejados.")
    if group == "total":
        bot._list_page(args, 0)
        return bot.BotReply("\n".join(lines), "consultar_informe", pagination={"page": 1, "pages": 1, "arguments": args})
    if group == "dia" and source == "pagos":
        rows = rows.annotate(dia_local=TruncDate("creado_en", tzinfo=timezone.get_current_timezone()))
    fields = GROUPS[source][group]
    metric = args.get("orden", "importe")
    grouped = rows.order_by().values(*fields).annotate(importe=Sum(amount), cantidad=Count("pk"), promedio=Avg(amount))
    grouped = grouped.order_by(("" if args.get("ascendente") else "-") + metric, fields[0])
    count_groups = grouped.count()
    if limit is not None:
        count_groups = min(limit, count_groups)
    page, pages, offset = bot._list_page(args, count_groups)
    label_group = {"punto_pago": "caja", "dia": "día"}.get(group, group)
    lines.append(f"Por {label_group}" + page_note(page, pages) + (f" · primeros {limit}" if limit and limit > 1 else ""))
    for row in grouped[offset:min(offset + bot.LIST_PAGE_SIZE, count_groups)]:
        if group == "dia":
            label = str(row[fields[0]])
        else:
            name = " ".join(str(row[field] or "") for field in fields[1:]).strip()
            label = f"#{row[fields[0]]} {name}" if row[fields[0]] is not None else (name or "Sin asignar")
        parts = [f"• {bot._list_text(label, 140)} · {bot._list_money(row['importe'])}"]
        if show_count:
            parts.append(f"{row['cantidad']} {source if row['cantidad'] != 1 else ('venta' if source == 'ventas' else 'pago')}")
        if show_average:
            parts.append(f"promedio {bot._list_money(row['promedio'])}")
        lines.append(" · ".join(parts))
    if not count_groups:
        lines.append("No hay registros en ese intervalo con esos filtros.")
    return bot.BotReply("\n".join(lines), "consultar_informe", pagination={"page": page, "pages": pages, "arguments": args})


def tool_brief(profile, arguments):
    from mainApp.models import Inventario, PedidoProveedor, TurnoCaja
    bot = _bot()
    bot._require_access(profile, "home")
    start, end = bot._date_range(arguments)
    args = dict(arguments, desde=start.isoformat(), hasta=end.isoformat())
    sections = args.get("secciones", ["ventas", "pagos", "balance", "inventario", "pedidos", "turnos"])
    if not sections or len(sections) != len(set(sections)):
        raise bot.TelegramBotError("Indica secciones distintas para el resumen.")
    permissions = {
        "ventas": ("metricas_negocio", "ventas_diarias"), "pagos": ("registrar_egreso",),
        "balance": ("metricas_negocio",), "inventario": ("visualizar_inventarios",),
        "pedidos": ("visualizar_pedidos",), "turnos": ("turnos_caja_dashboard", "turnos_caja_admin"),
    }
    lines = [f"Resumen del negocio · {start:%d/%m/%Y} al {end:%d/%m/%Y}"]
    omitted = []
    for section in sections:
        if not any(bot.user_can_access_url_name(profile.usuario, route) for route in permissions[section]):
            omitted.append(section)
            continue
        if section == "ventas":
            lines.append(bot.tool_sales(profile, {"desde": args["desde"], "hasta": args["hasta"]}))
        elif section == "pagos":
            lines.append(bot.tool_expenses(profile, {"desde": args["desde"], "hasta": args["hasta"]}))
        elif section == "balance":
            lines.append(bot.tool_balance(profile, {"desde": args["desde"], "hasta": args["hasta"]}))
        elif section == "inventario":
            rows = Inventario.objects.all()
            counts = rows.aggregate(agotados=Count("pk", filter=Q(cantidad__lte=0)), bajos=Count("pk", filter=Q(cantidad__gt=0, cantidad__lte=5)))
            lines.append(f"Inventario actual por sucursal: {counts['agotados']} sin existencias o en negativo; {counts['bajos']} con 1 a 5 unidades. No es histórico.")
        elif section == "pedidos":
            count = PedidoProveedor.objects.filter(estado="En espera").count()
            lines.append(f"Pedidos en espera actualmente: {count}.")
        else:
            counts = TurnoCaja.objects.aggregate(abiertos=Count("pk", filter=Q(estado="ABIERTO")), cierre=Count("pk", filter=Q(estado="CIERRE")))
            lines.append(f"Cajas actuales: {counts['abiertos']} abiertas; {counts['cierre']} en cierre.")
    if omitted:
        lines.append("Sin permiso para incluir: " + ", ".join(omitted) + ".")
    return bot.BotReply("\n\n".join(lines), "consultar_resumen_negocio", pagination={"page": 1, "pages": 1, "arguments": args})


def tool_pending(profile, arguments):
    from mainApp.models import TelegramAccionPendiente
    bot = _bot()
    bot._require_access(profile, "home")
    rows = TelegramAccionPendiente.objects.filter(telegram_usuario=profile, estado="PENDIENTE", vence_en__gt=timezone.now()).order_by("creado_en", "pk")
    count = rows.count()
    page, pages, offset = bot._list_page(arguments, count)
    lines = [f"Tienes {count} {'propuesta pendiente' if count == 1 else 'propuestas pendientes'}" + page_note(page, pages)]
    keyboard = []
    for index, action in enumerate(rows[offset:offset + bot.LIST_PAGE_SIZE], offset + 1):
        lines.append(f"{index}. {bot._list_text(action.resumen, 300)} · vence {timezone.localtime(action.vence_en):%H:%M}")
        # No confirma desde un resumen abreviado: debe revisar la propuesta original.
        keyboard.append([{"text": f"Cancelar propuesta {index}", "callback_data": f"cancel:{action.pk}"}])
    if count:
        lines.append("Confirma desde la propuesta original. /cancelar descarta todas las pendientes.")
    return bot.BotReply("\n".join(lines), "consultar_pendientes", reply_markup={"inline_keyboard": keyboard} if keyboard else None, pagination={"page": page, "pages": pages, "arguments": arguments})


def tool_multi(profile, arguments):
    from .telegram_operations import validate_arguments
    bot = _bot()
    queries = arguments["consultas"]
    if not 1 <= len(queries) <= 4:
        raise bot.TelegramBotError("Puedes combinar de 1 a 4 consultas por mensaje. Los cambios se preparan por separado.")
    plan = []
    for query in queries:
        name = query["herramienta"]
        if name not in READ_TOOLS:
            raise bot.TelegramBotError("Solo se pueden combinar consultas de lectura; no acciones ni otras consultas compuestas.")
        raw = query["argumentos_json"]
        if len(raw) > 4000:
            raise bot.TelegramBotError("Los filtros de la consulta son demasiado largos.")
        try:
            args = json.loads(raw)
        except (ValueError, TypeError):
            raise bot.TelegramBotError("No pude interpretar los filtros de una consulta. No ejecuté acciones.") from None
        validate_arguments(name, args)
        plan.append((name, args))
    lines, keyboard = [], []
    for index, (name, args) in enumerate(plan, 1):
        try:
            result = bot._execute_tool(profile, name, args)
        except (PermissionDenied, bot.TelegramBotError) as exc:
            lines.append(f"Consulta {index}: no completada. {exc}")
            continue
        lines.append(f"Consulta {index}\n{result.text}")
        for row in (result.reply_markup or {}).get("inline_keyboard", []):
            keyboard.append([dict(button, text=f"{index} · {button['text']}") for button in row])
    return bot.BotReply("\n\n".join(lines), "consultar_varias", reply_markup={"inline_keyboard": keyboard} if keyboard else None)


def resolve_continuation(profile, arguments):
    from mainApp.models import TelegramAuditoria
    from .telegram_operations import validate_arguments
    bot = _bot()
    bot._require_access(profile, "home")
    previous = TelegramAuditoria.objects.filter(
        usuario=profile.usuario, telegram_user_id=profile.telegram_user_id,
        telegram_chat_id=profile.telegram_chat_id, exitoso=True,
        creado_en__gte=timezone.now() - timedelta(hours=24),
        accion__in=(*READ_TOOLS, "consultar_varias"),
    )
    if arguments.get("herramienta"):
        previous = previous.filter(accion=arguments["herramienta"])
    last = previous.order_by("-pk").first()
    if last is None:
        raise bot.TelegramBotError("No hay una consulta reciente de tu cuenta y chat para continuar. Indica qué quieres consultar.")
    if last.accion == "consultar_varias":
        raise bot.TelegramBotError("La respuesta anterior contiene varias consultas. Indica cuál quieres continuar: ventas, pagos, inventario, etc.")
    args = dict(last.argumentos)
    changes = arguments.get("cambios", {})
    name = last.accion
    if name == "consultar_datos":
        changes = dict(changes)
        if "grupos" in changes:
            changes["agrupar"] = changes.pop("grupos")
        elif "agrupar" in changes:
            changes["agrupar"] = [changes["agrupar"]]
    if "agrupar" in changes and name in {"consultar_ventas", "consultar_pagos"}:
        args["fuente"] = "ventas" if name == "consultar_ventas" else "pagos"
        name = "consultar_informe"
        for key in ("detalle", "desglose_por_medio", "pagina"):
            args.pop(key, None)
    for key in ("usar_consulta_anterior",):
        args.pop(key, None)
    if name in DATE_TOOLS or (name == "consultar_datos" and SOURCES.get(args.get("fuente")) and SOURCES[args["fuente"]].date_field):
        args.setdefault("desde", timezone.localtime(last.creado_en).date().isoformat())
        args.setdefault("hasta", args["desde"])
    args.update(changes)
    if "desde" in changes and "hasta" not in changes:
        args["hasta"] = changes["desde"]
    navigation = arguments.get("navegacion")
    if navigation and changes:
        raise bot.TelegramBotError("Cambia los filtros o pide otra página en mensajes separados.")
    if navigation:
        if (
            name not in bot.PAGINATED_READ_TOOLS
            or args.get("solo_total") is True
            or (name == "consultar_informe" and args.get("agrupar", "total") == "total")
            or (name == "consultar_pagos" and not args.get("detalle"))
        ):
            raise bot.TelegramBotError("Esa consulta no tiene páginas.")
        args["pagina"] = int(args.get("pagina", 1)) + (1 if navigation == "siguiente" else -1)
    elif changes and name in bot.PAGINATED_READ_TOOLS:
        args["pagina"] = changes.get("pagina", 1)
    validate_arguments(name, args)
    return name, args


def _period_dates(period):
    today = timezone.localdate()
    if period == "hoy":
        return today, today
    if period == "ayer":
        return today - timedelta(days=1), today - timedelta(days=1)
    if period == "esta semana":
        return today - timedelta(days=today.weekday()), today
    if period == "este mes":
        return today.replace(day=1), today
    if period == "la semana pasada":
        end = today - timedelta(days=today.weekday() + 1)
        return end - timedelta(days=6), end
    if period == "el mes pasado":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    raise ValueError("Período no reconocido")


def common_read_request(text):
    """Atajos de solo lectura, estrictos: lo demás conserva la interpretación IA."""
    bot = _bot()
    normalized = re.sub(r"\s+", " ", bot._normalized_text(text)).strip(" ¿?¡!.")
    normalized = re.sub(r"^jarvis[, ]+", "", normalized)
    normalized = re.sub(r"^por favor[, ]+|[, ]+por favor$", "", normalized)
    sale = bot._sale_detail_request(normalized)
    if sale is not None:
        return "consultar_detalle_operativo", sale
    if normalized in {"resumen del negocio", "dame el resumen del negocio", "como va el negocio", "como vamos hoy"}:
        return "consultar_resumen_negocio", {}
    if normalized in {"mis pendientes", "muestrame mis pendientes", "acciones pendientes"}:
        return "consultar_pendientes", {}
    if normalized in {"mi horario", "muestrame mi horario", "cuando trabajo", "mis horarios", "cual es mi horario"}:
        return "consultar_horarios_empleados", {}
    if normalized in {"mi horario de hoy", "mi horario hoy", "cuando trabajo hoy"}:
        today = timezone.localdate().isoformat()
        return "consultar_horarios_empleados", {"desde": today, "hasta": today}
    if normalized in {"mi horario manana", "mi horario de manana", "cuando trabajo manana", "muestrame mi horario manana"}:
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        return "consultar_horarios_empleados", {"desde": tomorrow, "hasta": tomorrow}
    if normalized in {"mi horario esta semana", "mi horario de esta semana", "mis turnos de esta semana"}:
        today = timezone.localdate()
        start = today - timedelta(days=today.weekday())
        return "consultar_horarios_empleados", {"desde": start.isoformat(), "hasta": (start + timedelta(days=6)).isoformat()}
    if normalized in {"que puedes hacer", "que sabes hacer", "ayuda"}:
        return "consultar_capacidades", {}
    if normalized in {"siguiente", "siguiente pagina", "anterior", "pagina anterior"}:
        return "continuar_consulta", {"navegacion": "anterior" if "anterior" in normalized else "siguiente"}
    periods = r"hoy|ayer|esta semana|este mes|la semana pasada|el mes pasado"
    if re.fullmatch(r"(?:(?:muestrame|dame|quiero ver|ver) )?(?:la )?lista de (?:los )?empleados|(?:muestrame|dame|quiero ver|ver) (?:todos )?(?:los )?empleados", normalized):
        return "listar_empleados", {}
    payment_total = r"(?:cuanto (?:(?:he|hemos|se ha) pagado|pague|pagamos|se pago)|(?:(?:dame|muestrame|cual es) )?(?:el )?total (?:de )?(?:los )?pagos)"
    payment_list = r"(?:(?:muestrame|dame|lista|quiero ver|ver) (?:la lista de )?(?:los )?pagos|(?:la )?lista de (?:los )?pagos)"
    payment = re.fullmatch(
        r"(?P<request>" + payment_total + "|" + payment_list + r")"
        r"(?: (?:(?:de|del|durante|en) )?(?P<period>" + periods + r"))?"
        r"(?: (?P<breakdown>por (?:metodos? de pago|medios? de pago|medios?)))?"
        r"(?: en (?P<method>nequi|efectivo|tarjeta|caja social|banco caja social))?", normalized,
    )
    if payment:
        start, end = _period_dates(payment["period"] or "hoy")
        arguments = {"desde": start.isoformat(), "hasta": end.isoformat(),
                     "detalle": re.fullmatch(payment_list, payment["request"]) is not None,
                     "desglose_por_medio": bool(payment["breakdown"])}
        if payment["method"]:
            arguments["medio_pago"] = bot.normalize_payment_method_code(payment["method"])
        return "consultar_pagos", arguments
    sales = re.fullmatch(r"cuanto (?:vendimos|hemos vendido|se ha vendido|se vendio)(?: (" + periods + r"))?", normalized)
    if sales:
        start, end = _period_dates(sales[1] or "hoy")
        return "consultar_ventas", {"desde": start.isoformat(), "hasta": end.isoformat()}
    match = re.fullmatch(r"(?:y|ahora|pero) (" + periods + r")", normalized)
    if match:
        start, end = _period_dates(match[1])
        return "continuar_consulta", {"cambios": {"desde": start.isoformat(), "hasta": end.isoformat()}}
    match = re.fullmatch(r"(?:ahora|y) por (empleado|cliente|sucursal|dia|concepto|usuario|punto de pago)", normalized)
    if match:
        return "continuar_consulta", {"cambios": {"agrupar": match[1].replace(" ", "_")}}
    match = re.fullmatch(r"quien vendio mas(?: (" + periods + r"))?", normalized)
    if match:
        start, end = _period_dates(match[1] or "hoy")
        return "consultar_informe", {"fuente": "ventas", "agrupar": "empleado", "primeros": 1, "desde": start.isoformat(), "hasta": end.isoformat()}
    return None


DATE_FIELDS = {"desde": {"type": "STRING"}, "hasta": {"type": "STRING"}}
REPORT_FIELDS = {
    "fuente": {"type": "STRING", "enum": ["ventas", "pagos"]},
    "agrupar": {"type": "STRING", "enum": ["total", "empleado", "cliente", "sucursal", "punto_pago", "dia", "concepto", "usuario"]},
    **DATE_FIELDS,
    **{key: {"type": "STRING"} for key in ("sucursal", "empleado", "concepto", "usuario", "medio_pago")},
    "monto_min": {"type": "NUMBER"}, "monto_max": {"type": "NUMBER"},
    "comparar_anterior": {"type": "BOOLEAN", "description": "Compara el TOTAL con el período inmediatamente anterior de igual duración."},
    "incluir_total": {"type": "BOOLEAN", "description": "En resultados agrupados, añade total general solo si lo pide. false por defecto."},
    "incluir_cantidad": {"type": "BOOLEAN", "description": "Añade conteo de ventas/pagos solo si lo pide. false por defecto."},
    "incluir_promedio": {"type": "BOOLEAN", "description": "Añade promedio solo si lo pide. false por defecto."},
    "orden": {"type": "STRING", "enum": ["importe", "cantidad", "promedio"]},
    "ascendente": {"type": "BOOLEAN"}, "primeros": {"type": "INTEGER", "description": "De 1 a 50. Para quién vendió más, primeros=1."}, "pagina": {"type": "INTEGER"},
}
TOOL_DEFINITIONS = [
    {"name": "consultar_informe", "description": "Totales, promedios y rankings reales: ventas por EMPLEADO/cajero, cliente, sucursal, caja o día; pagos por concepto, usuario o día. Compara períodos. No calcula utilidad ni inventa saldos bancarios.", "parameters": {"type": "OBJECT", "properties": REPORT_FIELDS, "required": ["fuente"]}},
    {"name": "consultar_resumen_negocio", "description": "Resumen gerencial con las secciones pedidas y permitidas. Inventario, pedidos pendientes y turnos son el estado ACTUAL; ventas/pagos/balance usan las fechas. No tiene filtro de sucursal: los pagos no están asociados a una.", "parameters": {"type": "OBJECT", "properties": {**DATE_FIELDS, "secciones": {"type": "ARRAY", "items": {"type": "STRING", "enum": ["ventas", "pagos", "balance", "inventario", "pedidos", "turnos"]}}}}},
    {"name": "consultar_pendientes", "description": "Muestra solo las propuestas vigentes de la propia cuenta; no confirma ni ejecuta cambios.", "parameters": {"type": "OBJECT", "properties": {"pagina": {"type": "INTEGER"}}}},
    {"name": "consultar_varias", "description": "Responde de 1 a 4 consultas independientes de SOLO LECTURA en un mensaje, usando las funciones disponibles. No admite preparar/confirmar cambios ni consultas anidadas. Cada respuesta conserva sus permisos y botones.", "parameters": {"type": "OBJECT", "properties": {"consultas": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"herramienta": {"type": "STRING", "enum": list(READ_TOOLS)}, "argumentos_json": {"type": "STRING", "description": "Objeto JSON con los argumentos de esa herramienta, según su esquema; no SQL ni código."}}, "required": ["herramienta", "argumentos_json"]}}}, "required": ["consultas"]}},
    {"name": "continuar_consulta", "description": "Continúa una consulta real de la misma cuenta/chat en las últimas 24h. Conserva filtros/fechas; envía SOLO cambios explícitos. No hereda acciones. Si hubo varias consultas, especifica herramienta.", "parameters": {"type": "OBJECT", "properties": {
        "herramienta": {"type": "STRING", "enum": list(READ_TOOLS)},
        "navegacion": {"type": "STRING", "enum": ["siguiente", "anterior"]},
        "cambios": {"type": "OBJECT", "properties": {
            **REPORT_FIELDS,
            **{key: value for key, value in QUERY_DEFINITION["parameters"]["properties"].items() if key not in {"fuente", "desde", "hasta", "sucursal", "pagina", "agrupar"}},
            "grupos": QUERY_DEFINITION["parameters"]["properties"]["agrupar"],
            **{key: {"type": "STRING"} for key in ("consulta", "categoria", "estado", "cargo")},
            **{key: {"type": "BOOLEAN"} for key in ("detalle", "solo_total", "desglose_por_medio", "solo_bajo", "sin_ventas", "vinculado", "incluir_contacto")},
            "stock_max": {"type": "INTEGER"}, "monto_min": {"type": "NUMBER"}, "monto_max": {"type": "NUMBER"},
        }},
    }}},
]

TOOL_FUNCTIONS = {
    "consultar_informe": tool_report, "consultar_resumen_negocio": tool_brief,
    "consultar_pendientes": tool_pending, "consultar_varias": tool_multi,
    # _execute_tool resuelve la continuación y vuelve a comprobar la herramienta final.
    "continuar_consulta": resolve_continuation,
}
