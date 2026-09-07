"""Consultas combinables de solo lectura; el servidor construye ORM parametrizado.

Ni tablas, rutas ORM, SQL, expresiones ni nombres de agregados proceden del chat.
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from django.apps import apps
from django.db.models import Avg, Case, CharField, Count, DecimalField, ExpressionWrapper, F, Max, Min, Sum, Value, When
from django.utils.dateparse import parse_date

from .telegram_search import choose_match, rank_candidates, rank_queryset


@dataclass(frozen=True)
class Field:
    path: str
    label: str
    kind: str = "text"
    entity: str = ""


@dataclass(frozen=True)
class Source:
    model: str
    permissions: tuple
    fields: dict
    date_field: str = ""
    branch: str = ""
    note: str = ""


SOURCES = {
    "ventas": Source("Venta", ("metricas_negocio", "ventas_diarias"), {
        "id": Field("pk", "Venta", "id"), "importe": Field("total", "Total vendido", "money"),
        "fecha": Field("fecha", "Fecha", "date"), "sucursal": Field("sucursalid", "Sucursal", "entity", "Sucursal"),
        "empleado": Field("empleadoid", "Empleado", "entity", "Empleado"), "cliente": Field("clienteid", "Cliente", "entity", "Cliente"),
    }, "fecha", "sucursalid", "Son los totales actuales de las ventas, con cambios y devoluciones ya reflejados; no son utilidad ni saldo bancario."),
    "productos_vendidos": Source("DetalleVenta", ("metricas_negocio", "ventas_diarias"), {
        "id": Field("pk", "Detalle", "id"), "venta": Field("ventaid", "Venta", "id"),
        "producto": Field("productoid", "Producto", "entity", "Producto"), "cantidad": Field("cantidad", "Cantidad", "number"),
        "precio": Field("preciounitario", "Precio unitario", "money"), "importe": Field("_bot_line_total", "Importe de productos", "money"),
        "fecha": Field("ventaid__fecha", "Fecha", "date"), "sucursal": Field("ventaid__sucursalid", "Sucursal", "entity", "Sucursal"),
        "empleado": Field("ventaid__empleadoid", "Empleado", "entity", "Empleado"),
        "cliente": Field("ventaid__clienteid", "Cliente", "entity", "Cliente"),
    }, "ventaid__fecha", "ventaid__sucursalid", "Cantidades en la unidad registrada de cada producto. Los importes son cantidad × precio del renglón, sin descontar descuentos globales ni reintegros; no equivalen al total neto cobrado."),
    "pagos": Source("Egreso", ("registrar_egreso",), {
        "id": Field("pk", "Pago", "id"), "importe": Field("monto", "Total pagado", "money"),
        "concepto": Field("concepto", "Concepto", "entity", "ConceptoEgreso"),
        "usuario": Field("registrado_por_nombre", "Registró"), "medio_pago": Field("medio_pago", "Medio", "method"),
        "fecha": Field("creado_en__date", "Fecha", "date"),
    }, "creado_en__date", note="Son pagos registrados; no son transferencias ejecutadas por el bot."),
    "productos": Source("Producto", ("visualizar_productos", "generar_venta"), {
        "id": Field("pk", "Producto", "id"), "nombre": Field("nombre", "Nombre"),
        "precio": Field("precio", "Precio", "money"), "categoria": Field("categoria", "Categoría", "entity", "Categoria"),
        "codigo": Field("codigo_de_barras", "Código", "code"),
    }, note="Precios actuales del catálogo, no precios históricos de ventas."),
    "inventario": Source("Inventario", ("visualizar_inventarios",), {
        "id": Field("pk", "Inventario", "id"), "producto": Field("productoid", "Producto", "entity", "Producto"),
        "sucursal": Field("sucursalid", "Sucursal", "entity", "Sucursal"), "cantidad": Field("cantidad", "Existencias", "number"),
        "categoria": Field("productoid__categoria", "Categoría", "entity", "Categoria"),
    }, branch="sucursalid", note="Existencias actuales, no inventario histórico. Las cantidades usan la unidad registrada de cada producto."),
    "empleados": Source("Empleado", ("visualizar_empleados",), {
        "id": Field("pk", "Empleado", "id"), "nombre": Field("nombre", "Nombre"), "apellido": Field("apellido", "Apellido"),
        "cargo": Field("puesto", "Cargo"), "sucursal": Field("sucursalid", "Sucursal", "entity", "Sucursal"),
    }, branch="sucursalid"),
    "pedidos": Source("PedidoProveedor", ("visualizar_pedidos",), {
        "id": Field("pk", "Pedido", "id"), "proveedor": Field("proveedorid", "Proveedor", "entity", "Proveedor"),
        "estado": Field("estado", "Estado"), "importe": Field("costototal", "Costo", "money"),
        "fecha": Field("fechapedido", "Fecha", "date"), "sucursal": Field("sucursalid", "Sucursal", "entity", "Sucursal"),
    }, "fechapedido", "sucursalid"),
}

OPERATORS = {"igual": "exact", "distinto": "exact", "contiene": "icontains", "mayor": "gt", "menor": "lt", "al_menos": "gte", "hasta": "lte"}
AGGREGATES = {"sumar": Sum, "promedio": Avg, "minimo": Min, "maximo": Max}


def _bot():
    from . import telegram_bot
    return telegram_bot


def _entity_fields(model):
    return ("nombre", "apellido") if model in {"Empleado", "Cliente"} else ("nombre",)


def _display(field, value):
    bot = _bot()
    if value is None:
        return "Sin datos"
    if field.kind == "money":
        return bot._list_money(value)
    if field.kind == "method":
        return bot.payment_method_label(value)
    if field.kind == "entity":
        model = apps.get_model("mainApp", field.entity)
        row = model.objects.filter(pk=value).values_list(*_entity_fields(field.entity)).first()
        return f"#{value} " + (" ".join(str(part) for part in row if part) if row else "Sin nombre")
    if field.kind == "date":
        return value.strftime("%d/%m/%Y")
    return bot._list_text(value, 120)


def _filter(rows, eligible, field, operator, raw):
    bot = _bot()
    path, kind = field.path, field.kind
    if operator not in OPERATORS:
        raise bot.TelegramBotError("Ese tipo de filtro no está disponible.")
    value = str(raw).strip()
    if len(value) > 160 or not value:
        raise bot.TelegramBotError("Dime un valor de búsqueda de hasta 160 caracteres.")
    if kind in {"text", "entity", "method", "code"} and operator not in {"igual", "distinto", "contiene"}:
        raise bot.TelegramBotError("Para nombres usa igualdad o búsqueda por texto; para importes puedes usar mayor o menor.")
    if kind == "entity":
        if operator == "contiene":
            raise bot.TelegramBotError("Para identificar una persona, producto o sucursal, dime su nombre o ID. Para buscar por parte del nombre usa el listado de ese catálogo.")
        model = apps.get_model("mainApp", field.entity)
        candidates = model.objects.filter(pk__in=eligible.order_by().values(path))
        if value.isascii() and value.isdigit():
            if len(value) > 19:
                raise bot.TelegramBotError("Ese identificador es demasiado largo.")
            value = int(value)
            if not candidates.filter(pk=value).exists():
                return rows if operator == "distinto" else rows.none(), f"{field.label}: ID {value} (sin coincidencias en estos datos)"
        else:
            matches = rank_queryset(candidates, value, _entity_fields(field.entity))
            if not matches:
                return rows if operator == "distinto" else rows.none(), f"{field.label}: {raw} (sin coincidencias)"
            value = choose_match(value, matches, entity=field.label.lower()).pk
    elif kind in {"number", "money", "id"}:
        if operator == "contiene":
            raise bot.TelegramBotError("Los importes y las cantidades se comparan como números, no como texto.")
        try:
            value = Decimal(value)
            if not value.is_finite() or abs(value) >= Decimal("10000000000000000000"):
                raise ValueError
            if kind == "id" and (value != value.to_integral_value() or value <= 0):
                raise ValueError
        except (InvalidOperation, ValueError):
            raise bot.TelegramBotError("Dime un número válido para ese filtro.") from None
    elif kind == "date":
        try:
            value = parse_date(value)
        except ValueError:
            value = None
        if value is None or operator == "contiene":
            raise bot.TelegramBotError("Dime una fecha válida con año, mes y día.")
    elif kind == "method":
        canonical = bot._resolve_payment_method(value)
        codes = [code for code in eligible.order_by().values_list(path, flat=True).distinct() if bot.normalize_payment_method_code(code) == canonical]
        condition = {path + "__in": codes}
        filtered = rows.exclude(**condition) if operator == "distinto" else rows.filter(**condition)
        return filtered, f"{field.label}: {operator} {bot.payment_method_label(canonical)}"
    elif kind == "text" and operator in {"igual", "distinto"}:
        names = eligible.order_by().values_list(path, flat=True).distinct()
        matches = rank_candidates(value, ((name, name, ()) for name in names if name))
        if matches:
            value = choose_match(value, matches).label
    condition = {path + "__" + OPERATORS[operator]: value}
    return (rows.exclude(**condition) if operator == "distinto" else rows.filter(**condition)), f"{field.label}: {operator} {_display(field, value)}"


def tool_query(profile, arguments):
    bot = _bot()
    from .telegram_operations import _sale_branch_scope, validate_arguments
    validate_arguments("consultar_datos", arguments)
    source = SOURCES[arguments["fuente"]]
    bot._require_access(profile, *source.permissions)
    rows = apps.get_model("mainApp", source.model).objects.all()
    if source.model == "DetalleVenta":
        rows = rows.annotate(_bot_line_total=ExpressionWrapper(F("cantidad") * F("preciounitario"), output_field=DecimalField(max_digits=24, decimal_places=2)))
    if source.model in {"Venta", "DetalleVenta"}:
        rows = _sale_branch_scope(profile, rows, source.branch)
    args = dict(arguments)
    heading = ["Esto encontré en " + arguments["fuente"].replace("_", " ") + ":"]
    if args.get("sucursal"):
        if not source.branch:
            raise bot.TelegramBotError("Estos datos no están asociados a una sucursal.")
        branch = bot.resolve_name(apps.get_model("mainApp", "Sucursal").objects.filter(pk__in=rows.order_by().values(source.branch)), args["sucursal"], entity="una sucursal")
        rows = rows.filter(**{source.branch: branch.pk})
        args["sucursal"] = str(branch.pk)
        heading.append(f"Sucursal: {bot._list_text(branch.nombre)}")
    eligible = rows
    if source.date_field:
        start, end = bot._date_range(args)
        rows = rows.filter(**{source.date_field + "__range": (start, end)})
        args.update(desde=start.isoformat(), hasta=end.isoformat())
        heading.append(f"Del {start:%d/%m/%Y} al {end:%d/%m/%Y}")
    elif args.get("desde") or args.get("hasta"):
        raise bot.TelegramBotError("Este catálogo muestra datos actuales, no históricos. Puedes consultar las ventas para revisar otro intervalo.")
    filters = args.get("filtros", [])
    groups = args.get("agrupar", [])
    if len(filters) > 8 or len(groups) > 2 or len(groups) != len(set(groups)):
        raise bot.TelegramBotError("Puedes combinar hasta ocho filtros y dos agrupaciones distintas por consulta.")
    for item in filters:
        field = source.fields.get(item["campo"])
        if field is None:
            raise bot.TelegramBotError("Ese dato no se puede consultar aquí. Campos disponibles: " + ", ".join(source.fields))
        rows, label = _filter(rows, eligible, field, item["operador"], item["valor"])
        heading.append(label)
    operation = args.get("operacion", "listar")
    measure = source.fields.get(args.get("campo", ""))
    if args.get("campo") and measure is None:
        raise bot.TelegramBotError("Ese campo no está disponible en esta consulta.")
    if operation == "listar" and args.get("campo"):
        raise bot.TelegramBotError("Para elegir un importe o cantidad indica qué cálculo quieres hacer.")
    if operation in AGGREGATES and (measure is None or measure.kind not in {"money", "number"}):
        raise bot.TelegramBotError("Dime qué importe o cantidad quieres sumar o comparar; los nombres e identificadores no se suman.")
    if operation == "listar" and groups:
        raise bot.TelegramBotError("Para agrupar indica si quieres contar, sumar o calcular un promedio.")
    group_fields = []
    for name in groups:
        field = source.fields.get(name)
        if field is None or field.kind not in {"entity", "text", "date", "method"}:
            raise bot.TelegramBotError("Esa agrupación no está disponible para estos datos.")
        if field.kind == "method":
            codes = list(rows.order_by().values_list(field.path, flat=True).distinct())
            rows = rows.annotate(_bot_method=Case(
                *(When(**{field.path: code}, then=Value(bot.normalize_payment_method_code(code))) for code in codes),
                default=F(field.path), output_field=CharField(),
            ))
            field = Field("_bot_method", field.label, "method")
        group_fields.append(field)
    if operation == "listar":
        selected = list(source.fields.values())[:6]
        count = rows.count()
        page, pages, offset = bot._list_page(args, count)
        order_name = args.get("ordenar", "id")
        if order_name not in source.fields:
            raise bot.TelegramBotError("No puedo ordenar por ese dato.")
        order = ("-" if args.get("descendente", False) else "") + source.fields[order_name].path
        values = rows.order_by(order, "pk").values(*[field.path for field in selected])[offset:offset + bot.LIST_PAGE_SIZE]
        heading.append(f"{count} registros · página {page} de {pages}")
        for row in values:
            heading.append("• " + " · ".join(f"{field.label}: {_display(field, row[field.path])}" for field in selected))
    else:
        aggregate = (Count(measure.path, distinct=True) if measure else Count("pk")) if operation == "contar" else AGGREGATES[operation](measure.path)
        output = Field("_value", {"contar": "Cantidad de registros", "sumar": "Total", "promedio": "Promedio", "minimo": "Mínimo", "maximo": "Máximo"}[operation], "number" if operation == "contar" else measure.kind)
        if operation == "contar" and measure:
            output = Field("_value", f"{measure.label}: valores distintos", "number")
        if group_fields:
            grouped = rows.order_by().values(*[field.path for field in group_fields]).annotate(_value=aggregate)
            count = grouped.count()
            page, pages, offset = bot._list_page(args, count)
            order = "_value"
            if args.get("ordenar"):
                if args["ordenar"] not in groups:
                    raise bot.TelegramBotError("En un resultado agrupado puedes ordenar por el resultado o por una de las agrupaciones.")
                order = group_fields[groups.index(args["ordenar"])].path
            grouped = grouped.order_by(("-" if args.get("descendente", True) else "") + order, *[field.path for field in group_fields])
            heading.append(f"{count} grupos · página {page} de {pages}")
            for row in grouped[offset:offset + bot.LIST_PAGE_SIZE]:
                label = " · ".join(f"{field.label}: {_display(field, row[field.path])}" for field in group_fields)
                heading.append(f"• {label} · {output.label}: {_display(output, row['_value'])}")
        else:
            if args.get("ordenar"):
                raise bot.TelegramBotError("Un total único no necesita orden; pide una lista o una agrupación.")
            page, pages, _ = bot._list_page(args, 0)
            totals = rows.aggregate(_value=aggregate, _records=Count("pk"))
            count, value = totals["_records"], totals["_value"]
            value = 0 if value is None and operation == "sumar" else value
            heading.append(f"{output.label}: {_display(output, value)} · {count} registros")
    if not count:
        heading.append("No encontré resultados con esos datos.")
    if source.note:
        heading.append(source.note)
    args["pagina"] = page
    return bot.BotReply("\n".join(heading), "consultar_datos", pagination={"page": page, "pages": pages, "arguments": args})


QUERY_DEFINITION = {
    "name": "consultar_datos",
    "description": "Consultas de solo lectura con hasta 8 filtros AND, sumas, conteos, promedios, mínimo/máximo y hasta 2 agrupaciones. Contar con campo cuenta valores distintos; sin campo cuenta registros. Usa esta herramienta cuando los informes simples no cubren la petición. No recibe SQL. " + "; ".join(f"{name}: {', '.join(source.fields)}" for name, source in SOURCES.items()),
    "parameters": {"type": "OBJECT", "properties": {
        "fuente": {"type": "STRING", "enum": list(SOURCES)},
        "operacion": {"type": "STRING", "enum": ["listar", "contar", *AGGREGATES]},
        "campo": {"type": "STRING", "description": "Cantidad o importe que se agrega, según los campos de la fuente."},
        "filtros": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "campo": {"type": "STRING"}, "operador": {"type": "STRING", "enum": list(OPERATORS)},
            "valor": {"type": "STRING", "description": "Número, fecha AAAA-MM-DD o nombre/ID real. No SQL ni rutas ORM."},
        }, "required": ["campo", "operador", "valor"]}},
        "agrupar": {"type": "ARRAY", "items": {"type": "STRING"}},
        "desde": {"type": "STRING"}, "hasta": {"type": "STRING"}, "sucursal": {"type": "STRING"},
        "ordenar": {"type": "STRING", "description": "Campo de la fuente para listas; los agregados se ordenan por su resultado."},
        "descendente": {"type": "BOOLEAN"}, "pagina": {"type": "INTEGER"},
    }, "required": ["fuente"]},
}

SMART_QUERY_RULES = (
    "Antes de decir que no puedes, comprueba si la petición se resuelve con una consulta simple, consultar_datos, "
    "consultar_varias o una propuesta de cambio permitida. Descompón mentalmente fuente, fechas, filtros, agrupación y cálculo. "
    "Para preguntas analíticas no cubiertas por informes simples usa consultar_datos: el servidor genera consultas seguras, nunca envíes SQL. "
    "Ejemplo: unidades de arroz Diana este mes por sucursal => fuente=productos_vendidos, operacion=sumar, campo=cantidad, "
    "filtro producto igual 'arroz Diana', agrupar=['sucursal'] y fechas reales. Para personas distintas usa contar con campo=cliente/empleado. "
    "Envía los nombres tal como los dice la persona: las herramientas buscan acentos, palabras intercambiadas y errores de escritura. "
    "No inventes IDs ni traduzcas códigos de barras; si el servidor encuentra nombres ambiguos pide elegir un ID. "
    "No combines personas, presentaciones o productos distintos para forzar una coincidencia. Un ID explícito nunca se sustituye por similitud. "
    "Un nombre parecido puede preparar un cambio mostrando el nombre real, pero no lo guarda: requiere confirmar. "
    "Mantén todos los filtros solicitados. Si un dato o acción no está admitido, explica qué parte sí puedes resolver y pregunta lo que falta; "
    "no inventes resultados, nuevas capacidades, fórmulas ni escrituras. En eventos indica siempre desde/hasta según las fechas solicitadas. "
    "Para seguir un análisis con dos agrupaciones usa continuar_consulta, cambios.grupos. Las consultas de productos_vendidos no son importe neto cobrado."
)
