"""Planes acotados y referencias verificables; no ejecuta SQL ni código del modelo."""
import json
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .telegram_business import PREPARATION_TOOLS, definition, TEXT


def _bot():
    from . import telegram_bot
    return telegram_bot


def tool_result(profile, args, update=None):
    from mainApp.models import TelegramAuditoria
    from .telegram_assistant import READ_TOOLS
    bot = _bot()
    rows = TelegramAuditoria.objects.filter(
        usuario_id=profile.usuario_id, telegram_user_id=profile.telegram_user_id,
        telegram_chat_id=profile.telegram_chat_id, exitoso=True,
        accion__in=set(READ_TOOLS) - {"consultar_resultado"},
        creado_en__gte=max(profile.vinculado_en, timezone.now() - timedelta(hours=24)),
    ).order_by("-creado_en", "-pk")
    refs = None
    # Nunca saltar una lista nueva no seleccionable y acabar usando otra vieja.
    for row in rows[:1]:
        try:
            data = json.loads(row.detalle)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("referencias"):
            refs = data["referencias"]
            break
    position = args["posicion"]
    if not refs or not 1 <= position <= len(refs):
        raise bot.TelegramClarification("No tengo esa posición en la última lista que te mostré. Pídeme la lista de nuevo o dime el ID.")
    ref = refs[position - 1]
    entity = ref["entidad"]
    if args.get("entidad") and args["entidad"] != entity:
        raise bot.TelegramClarification("La última lista era de otro tipo de registro. Dime el ID o vuelve a pedir la lista que necesitas.")
    # Cada lectura vuelve a comprobar permisos y alcance. Un resultado pasado
    # no concede acceso a un registro que ya no se puede consultar.
    if entity == "pagos":
        name, params = "consultar_pago", {"pago_id": int(ref["id"])}
    elif entity == "empleados":
        name, params = "listar_empleados", {"consulta": str(ref["id"])}
    else:
        from .telegram_operations import RESOURCES
        if entity not in RESOURCES:
            raise bot.TelegramClarification("Para ese resultado, dime su ID y qué necesitas consultar.")
        name, params = "consultar_registros", {"recurso": entity, "registro_id": str(ref["id"])}
    # No crear una nueva lista de un elemento en el historial: de otro modo
    # preguntar por el primero cambiaría el significado posterior de «segundo».
    reply = bot.TOOL_FUNCTIONS[name](profile, params)
    reply = reply if isinstance(reply, bot.BotReply) else bot.BotReply(str(reply), "consultar_resultado")
    if entity != "pagos" and not reply.references:
        raise bot.TelegramClarification("Ese registro ya no está disponible. Pídeme la lista actualizada.")
    reply.references = [ref]
    return reply


def _binding_entity(name, args, field):
    if name == "preparar_cambio_catalogo" and field == "registro_id":
        return {"producto": "productos", "proveedor": "proveedores", "cliente": "clientes", "empleado": "empleados", "categoria": "categorias", "sucursal": "sucursales"}.get(args.get("entidad")), int
    if name == "preparar_alias" and field == "registro_id":
        return {"producto": "productos", "proveedor": "proveedores", "cliente": "clientes", "empleado": "empleados", "categoria": "categorias", "sucursal": "sucursales"}.get(args.get("entidad")), int
    allowed = {
        "preparar_movimiento_inventario": {"producto": ("productos", str), "sucursal": ("sucursales", str)},
        "preparar_pedido_proveedor": {"proveedor": ("proveedores", str), "sucursal": ("sucursales", str)},
        "preparar_edicion_pago": {"pago_id": ("pagos", int)},
        "preparar_devolucion_venta": {"venta_id": ("ventas", int)},
        "consultar_detalle_operativo": {"id": ({"venta": "ventas", "pedido": "pedidos"}.get(args.get("tipo")), int)},
        "consultar_pago": {"pago_id": ("pagos", int)},
    }
    return allowed.get(name, {}).get(field, (None, str))


def tool_workflow(profile, args, update=None):
    """Una interpretación, hasta cinco herramientas; sin bucle de IA ni auto-confirmar."""
    from .telegram_assistant import READ_TOOLS
    from .telegram_operations import validate_arguments
    from .telegram_ai_output import strict_json
    bot = _bot()
    steps = args["pasos"]
    if not 1 <= len(steps) <= 5:
        raise bot.TelegramClarification("Pídeme como máximo cinco pasos y un solo cambio por mensaje.")
    write_tools = PREPARATION_TOOLS | {"preparar_registro_pago", "preparar_edicion_pago", "preparar_cambio_catalogo", "preparar_devolucion_venta", "preparar_turno_empleado"}
    reads = set(READ_TOOLS) | {"consultar_capacidades"}
    plans, seen = [], set()
    for index, step in enumerate(steps):
        name = step["herramienta"]
        if name not in reads | write_tools or (name in write_tools and index != len(steps) - 1):
            raise bot.TelegramBotError("Solo puedo consultar y preparar un cambio al final. No ejecuté el plan.")
        try:
            params = strict_json(step["argumentos_json"])
        except ValueError:
            raise bot.TelegramBotError("No entendí los datos de uno de los pasos. No ejecuté el plan.") from None
        if not isinstance(params, dict):
            raise bot.TelegramBotError("Los datos de cada paso deben corresponder a una herramienta permitida.")
        bindings = step.get("referencias", [])
        fields = set()
        for binding in bindings:
            field = binding["campo"]
            entity, _ = _binding_entity(name, params, field)
            if not entity or field in fields or field in params or not 1 <= binding["paso"] <= index:
                raise bot.TelegramBotError("No puedo identificar con seguridad el registro de ese paso. Dime su ID.")
            fields.add(field)
        # La validación completa se repite tras resolver referencias reales.
        validate_arguments(name, params, allow_missing=bool(bindings))
        fingerprint = json.dumps(step, sort_keys=True)
        if fingerprint in seen:
            raise bot.TelegramBotError("El plan repite una operación. Pídeme el cambio una sola vez.")
        seen.add(fingerprint)
        plans.append((name, params, bindings))
    results = []
    for name, params, bindings in plans:
        for binding in bindings:
            references = results[binding["paso"] - 1].references or []
            entity, cast = _binding_entity(name, params, binding["campo"])
            if len(references) != 1 or references[0]["entidad"] != entity:
                # No escoger el primero de varias coincidencias para modificarlo.
                raise bot.TelegramClarification("Encontré más de una opción o ninguna coincidencia segura. Dime el ID del registro que quieres usar; no preparé cambios.")
            params[binding["campo"]] = cast(references[0]["id"])
        result = bot._execute_tool(profile, name, params, update)
        results.append(result)
        if name in write_tools:
            # Conservar la identidad y el detalle original de la confirmación.
            return result
    text = "\n\n".join(result.text for result in results)
    buttons = [row for result in results for row in (result.reply_markup or {}).get("inline_keyboard", [])]
    return bot.BotReply(text, "resolver_tarea", reply_markup={"inline_keyboard": buttons} if buttons else None)


def tool_analyze_balance(profile, args):
    from mainApp.models import Egreso, Venta
    bot = _bot()
    bot._require_access(profile, "metricas_negocio")
    start, end = bot._date_range(args)
    span = end - start + timedelta(days=1)
    def totals(first, last):
        sold = Venta.objects.filter(fecha__range=(first, last)).aggregate(v=Sum("total"))["v"] or Decimal(0)
        paid = Egreso.objects.filter(creado_en__date__range=(first, last)).aggregate(v=Sum("monto"))["v"] or Decimal(0)
        return sold, paid
    sold, paid = totals(start, end)
    old_sold, old_paid = totals(start - span, start - timedelta(days=1))
    lines = [f"Del {start:%d/%m} al {end:%d/%m}: vendido {bot._list_money(sold)}, pagado {bot._list_money(paid)}. Queda {bot._list_money(sold-paid)}.",
             f"Frente al período anterior de igual duración ({start-span:%d/%m}–{start-timedelta(days=1):%d/%m}): las ventas cambiaron {bot._list_money(sold-old_sold)} y los pagos {bot._list_money(paid-old_paid)}; el balance cambió {bot._list_money((sold-paid)-(old_sold-old_paid))}."]
    top = Egreso.objects.filter(creado_en__date__range=(start, end)).values("concepto__nombre").annotate(total=Sum("monto")).order_by("-total", "concepto__nombre")[:3]
    if top:
        lines.append("Mayores pagos del período: " + "; ".join(f"{item['concepto__nombre']}: {bot._list_money(item['total'])}" for item in top) + ".")
    lines.append("Son movimientos registrados, no utilidad ni saldo bancario. Esta comparación no demuestra por sí sola la causa de una diferencia de caja.")
    return "\n".join(lines)


TOOL_DEFINITIONS = [
    definition("consultar_resultado", "Consulta la posición 1, 2, etc. de la ÚLTIMA lista real mostrada a esta cuenta/chat. Úsala para 'el segundo'; no inventar IDs. Revalida permisos; no modifica.", {"posicion": {"type": "INTEGER"}, "entidad": TEXT}, ["posicion"]),
    definition("analizar_balance", "Explica ventas menos pagos y su cambio frente al período anterior de igual duración, con mayores pagos. No demuestra fraude, causalidad, utilidad ni saldo real.", {"desde": TEXT, "hasta": TEXT}),
    definition("resolver_tarea", "Encadena hasta 5 herramientas disponibles de lectura y, opcionalmente, UNA preparación al FINAL. No confirmar ni anidar planes/consultar_varias/continuar_consulta. Cada argumentos_json sigue el esquema real de su herramienta. Referencias permite omitir un campo ID y tomarlo de un paso anterior SOLO si produjo UN único registro. Nunca inventar cantidades/precios/motivos; preguntar si faltan. Para 'el segundo', consultar_resultado primero. El servidor devuelve resultados, o propuesta con botones.", {
        "pasos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "herramienta": TEXT, "argumentos_json": TEXT,
            "referencias": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"campo": TEXT, "paso": {"type": "INTEGER"}}, "required": ["campo", "paso"]}},
        }, "required": ["herramienta", "argumentos_json"]}},
    }, ["pasos"]),
]
TOOL_FUNCTIONS = {"consultar_resultado": tool_result, "analizar_balance": tool_analyze_balance, "resolver_tarea": tool_workflow}
