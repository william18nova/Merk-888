"""Operaciones explícitamente permitidas al bot; nunca SQL ni modelos arbitrarios.

Las lecturas proyectan solo campos públicos de cada vista. Las modificaciones
reutilizan los formularios web y se guardan exclusivamente tras confirmación.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Exists, F, OuterRef, Q, Sum
from django.forms.models import model_to_dict
from django.urls import reverse
from django.utils import timezone

from .telegram_returns import RETURN_TOOL_DEFINITION, tool_prepare_return


@dataclass(frozen=True)
class Resource:
    model: str
    permission: str
    # (etiqueta, ruta ORM fija, formato)
    fields: tuple
    search: tuple = ()
    branch: str = ""
    date_field: str = ""
    status: str = ""
    amount: str = ""
    contact: tuple = ()


def cols(*items):
    return tuple(tuple(item.split(":")) if item.count(":") == 2 else (*item.split(":"), "text") for item in items)


RESOURCES = {
    "productos": Resource("Producto", "visualizar_productos", cols("Nombre:nombre", "Precio:precio:money", "Categoría:categoria__nombre", "Barras:codigo_de_barras"), ("nombre", "codigo_de_barras")),
    "categorias": Resource("Categoria", "visualizar_categorias", cols("Nombre:nombre", "Descripción:descripcion"), ("nombre",)),
    "sucursales": Resource("Sucursal", "visualizar_sucursales", cols("Nombre:nombre", "Dirección:direccion", "Teléfono:telefono"), ("nombre",)),
    "proveedores": Resource("Proveedor", "visualizar_proveedores", cols("Nombre:nombre", "Empresa:empresa"), ("nombre", "empresa"), contact=cols("Teléfono:telefono", "Correo:email")),
    "clientes": Resource("Cliente", "visualizar_clientes", cols("Nombre:nombre", "Apellido:apellido"), ("nombre", "apellido"), contact=cols("Teléfono:telefono", "Correo:email")),
    "puntos_pago": Resource("PuntosPago", "visualizar_puntos_pago", cols("Nombre:nombre", "Sucursal:sucursalid__nombre", "Descripción:descripcion"), ("nombre",), branch="sucursalid"),
    "precios_proveedor": Resource("PreciosProveedor", "visualizar_productos_precios_proveedores", cols("Producto:productoid__nombre", "Proveedor:proveedorid__nombre", "Precio:precio:money"), ("productoid__nombre", "proveedorid__nombre")),
    "inventario": Resource("Inventario", "visualizar_inventarios", cols("Producto:productoid__nombre", "ID producto:productoid_id", "Sucursal:sucursalid__nombre", "Cantidad:cantidad"), ("productoid__nombre", "productoid__codigo_de_barras"), branch="sucursalid"),
    "horarios_negocio": Resource("HorariosNegocio", "visualizar_horarios", cols("Día:dia_semana", "Sucursal:sucursalid__nombre", "Apertura:horaapertura", "Cierre:horacierre"), ("dia_semana",), branch="sucursalid"),
    "horarios_caja": Resource("HorarioCaja", "visualizar_horarios_cajas", cols("Día:dia_semana", "Caja:puntopagoid__nombre", "Apertura:horaapertura", "Cierre:horacierre"), ("dia_semana", "puntopagoid__nombre"), branch="puntopagoid__sucursalid"),
    "ventas": Resource("Venta", "visualizar_ventas", cols("Fecha:fecha", "Cliente:clienteid__nombre", "Sucursal:sucursalid__nombre", "Total:total:money"), ("clienteid__nombre", "clienteid__apellido", "empleadoid__nombre"), branch="sucursalid", date_field="fecha", amount="total"),
    "pedidos": Resource("PedidoProveedor", "visualizar_pedidos", cols("Proveedor:proveedorid__nombre", "Fecha:fechapedido", "Estado:estado", "Costo:costototal:money"), ("proveedorid__nombre",), branch="sucursalid", date_field="fechapedido", status="estado", amount="costototal"),
    "devoluciones": Resource("CambioDevolucion", "visualizar_cambios", cols("Producto:productoid__nombre", "Cantidad:cantidad", "Tipo:tipo", "Estado:estado", "Fecha:fecha"), ("productoid__nombre", "motivo"), date_field="fecha", status="estado"),
    "reintegros": Resource("ReintegroVenta", "ver_venta", cols("Venta:venta_id", "Monto:monto:money", "Medio:medio_pago:method", "Usuario:registrado_por__nombreusuario", "Fecha:creado_en"), branch="venta__sucursalid", date_field="creado_en__date", amount="monto"),
    "nequi": Resource("NotificacionNequi", "nequi_notificaciones", cols("Remitente:remitente", "Monto:monto:money", "Recibido:recibido_en", "Venta vinculada:venta_id"), ("remitente", "referencia"), date_field="recibido_en__date", amount="monto"),
    "usuarios": Resource("Usuario", "visualizar_usuarios", cols("Usuario:nombreusuario", "Rol:rolid__nombre", "Activo:is_active"), ("nombreusuario", "rolid__nombre"), status="is_active"),
    "roles": Resource("Rol", "visualizar_roles", cols("Nombre:nombre", "Descripción:descripcion"), ("nombre",)),
    "permisos": Resource("Permiso", "visualizar_permisos", cols("Nombre:nombre", "Descripción:descripcion"), ("nombre", "descripcion")),
    "metodos_pago": Resource("MetodoPago", "configuracion_metodos_pago", cols("Nombre:nombre", "Activo:activo", "Orden:orden"), ("nombre", "codigo"), status="activo"),
    "funcionalidades": Resource("ConfiguracionFuncionalidad", "configuracion_funcionalidades", cols("Función:clave", "Habilitada:habilitada", "Último cambio:actualizada_en"), ("clave",), status="habilitada"),
    "carritos_abandonados": Resource("VentaCarritoAudit", "ventas_no_realizadas", cols("Usuario:usuario_nombre", "Sucursal:sucursal_nombre", "Total:total:money", "Productos:cantidad_productos", "Fecha:creado_en"), ("usuario_nombre", "cliente_nombre"), branch="sucursalid", date_field="creado_en__date", amount="total"),
    "conceptos_pago": Resource("ConceptoEgreso", "registrar_egreso", cols("Concepto:nombre", "Creado:creado_en"), ("nombre",)),
}


def _bot():
    # Importación tardía: telegram_bot registra estas funciones al importarse.
    from . import telegram_bot
    return telegram_bot


def _model(name):
    return apps.get_model("mainApp", name)


def _pk(model, value):
    try:
        if value in (None, "") or isinstance(value, bool):
            raise ValueError
        key = model._meta.pk.to_python(value)
        model._meta.pk.run_validators(key)
        return key
    except (TypeError, ValueError, ValidationError):
        raise _bot().TelegramBotError("Indica un ID válido del registro.") from None


def _format(value, kind="text"):
    bot = _bot()
    if value is None or value == "":
        return "—"
    if kind == "money":
        return bot._list_money(value)
    if kind == "method":
        return bot.payment_method_label(value)
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, datetime):
        return timezone.localtime(value).strftime("%d/%m/%Y %H:%M")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return bot._list_text(str(value), limit=100)


def _page_reply(tool, args, heading, queryset, fields, summary=""):
    bot = _bot()
    total = queryset.count()
    page, pages, offset = bot._list_page(args, total)
    lines = [heading, f"{total} registro(s) · página {page} de {pages}"]
    if summary:
        lines.append(summary)
    for row in queryset.values("pk", *(path for _, path, _ in fields))[offset:offset + bot.LIST_PAGE_SIZE]:
        lines.append(f"\n• #{row['pk']} " + " · ".join(f"{label}: {_format(row[path], kind)}" for label, path, kind in fields))
    if not total:
        lines.append("No hay registros con esos filtros.")
    return bot.BotReply("\n".join(lines), tool, pagination={"page": page, "pages": pages, "arguments": bot._json_safe(args)})


def tool_records(profile, arguments):
    bot = _bot()
    args = dict(arguments)
    name = args.get("recurso")
    spec = RESOURCES.get(name)
    if spec is None:
        raise bot.TelegramBotError("Catálogo no permitido. Usa /acciones para ver las consultas disponibles.")
    bot._require_access(profile, spec.permission)
    model = _model(spec.model)
    rows = model.objects.all()
    heading = name.replace("_", " ").capitalize()
    if args.get("registro_id") not in (None, ""):
        rows = rows.filter(pk=_pk(model, args["registro_id"]))
    query = str(args.get("consulta") or "").strip()[:120]
    if query:
        if not spec.search:
            raise bot.TelegramBotError("Este listado se filtra por ID o fecha, no por nombre.")
        condition = Q()
        for field in spec.search:
            condition |= Q(**{field + "__icontains": query})
        rows = rows.filter(condition)
        heading += f" · búsqueda: {bot._list_text(query, 60)}"
    if args.get("sucursal"):
        if not spec.branch:
            raise bot.TelegramBotError("Este catálogo no tiene filtro de sucursal.")
        branch = bot._find_branch(args["sucursal"])
        rows = rows.filter(**{spec.branch: branch.pk})
        args["sucursal"] = str(branch.pk)
        heading += f" · sucursal: {bot._list_text(branch.nombre, 60)}"
    if spec.date_field and (not args.get("registro_id") or args.get("desde") or args.get("hasta")):
        start, end = bot._date_range(args)
        args.update(desde=start.isoformat(), hasta=end.isoformat())
        rows = rows.filter(**{spec.date_field + "__range": (start, end)})
        heading += f" · {start:%d/%m/%Y} a {end:%d/%m/%Y}"
    elif not spec.date_field and (args.get("desde") or args.get("hasta")):
        raise bot.TelegramBotError("Este catálogo no admite fechas. Para ventas históricas usa ventas o ranking_productos.")
    if args.get("estado"):
        if not spec.status:
            raise bot.TelegramBotError("Este listado no tiene filtro de estado.")
        value = bot._normalized_text(args["estado"])
        field = model._meta.get_field(spec.status)
        if field.get_internal_type() == "BooleanField":
            choices = {"activo": True, "activa": True, "habilitada": True, "si": True, "inactivo": False, "inactiva": False, "deshabilitada": False, "no": False}
        else:
            choices = {bot._normalized_text(key): key for key, _ in field.choices}
        if value not in choices:
            raise bot.TelegramBotError("Estado inválido. Opciones: " + ", ".join(choices))
        rows = rows.filter(**{spec.status: choices[value]})
        heading += f" · estado: {value}"
    if args.get("sin_ventas"):
        if name != "productos":
            raise bot.TelegramBotError("Sin ventas solo aplica al catálogo de productos.")
        sold = _model("DetalleVenta").objects.filter(productoid=OuterRef("pk"))
        rows = rows.annotate(_sold=Exists(sold)).filter(_sold=False)
        heading += " · sin ninguna venta registrada en el historial disponible"
    if args.get("categoria"):
        if name not in {"productos", "inventario", "precios_proveedor"}:
            raise bot.TelegramBotError("El filtro de categoría aplica a productos, inventario y precios de proveedor.")
        raw_category = str(args["categoria"]).strip()
        category_model = _model("Categoria")
        category_rows = category_model.objects.filter(pk=int(raw_category)) if raw_category.isdecimal() and len(raw_category) < 12 else category_model.objects.filter(nombre__iexact=raw_category)
        categories = list(category_rows[:2])
        if len(categories) != 1:
            raise bot.TelegramBotError("No encontré una categoría única. Consulta las categorías e indica su ID.")
        category = categories[0]
        rows = rows.filter(**{"categoria" if name == "productos" else "productoid__categoria": category.pk})
        args["categoria"] = str(category.pk)
        heading += f" · categoría: {_format(category.nombre)}"
    if args.get("stock_max") is not None:
        if name != "inventario":
            raise bot.TelegramBotError("Stock máximo solo aplica al inventario.")
        try:
            value = Decimal(str(args["stock_max"]))
            if not value.is_finite() or value != value.to_integral_value() or abs(value) > 2147483647:
                raise ValueError
        except (ValueError, ArithmeticError):
            raise bot.TelegramBotError("El stock máximo debe ser un entero válido.") from None
        rows = rows.filter(cantidad__lte=int(value))
        heading += f" · existencias ≤ {value}"
    if name == "nequi":
        rows = rows.filter(es_ingreso=True)
        heading += " · solo dinero recibido"
    if args.get("vinculado") is not None:
        if name != "nequi" or not isinstance(args["vinculado"], bool):
            raise bot.TelegramBotError("Vinculado solo aplica a Nequi y debe ser verdadero o falso.")
        rows = rows.filter(venta__isnull=not args["vinculado"])
        heading += " · " + ("vinculados" if args["vinculado"] else "no vinculados")
    fields = spec.fields + (spec.contact if args.get("incluir_contacto") is True else ())
    summary = f"Total del listado: {bot._list_money(rows.aggregate(value=Sum(spec.amount))['value'])}" if spec.amount else ""
    if args.get("solo_total") is True:
        return bot.BotReply(f"{heading}\n{rows.count()} registro(s)" + (f"\n{summary}" if summary else ""), "consultar_registros")
    ordering = ("-" + spec.date_field.removesuffix("__date"), "-pk") if spec.date_field else ("pk",)
    return _page_reply("consultar_registros", args, heading, rows.order_by(*ordering), fields, summary)


def tool_detail(profile, arguments):
    bot = _bot()
    kind = arguments.get("tipo")
    specs = {
        "venta": ("Venta", ("ver_venta",), "DetalleVenta", "ventaid"),
        "pedido": ("PedidoProveedor", ("ver_pedido",), "DetallePedidoProveedor", "pedidoid"),
        "turno": ("TurnoCaja", ("turnos_caja_admin", "turnos_caja_dashboard"), "TurnoCajaMedio", "turno"),
    }
    if kind not in specs:
        raise bot.TelegramBotError("Puedes consultar el detalle de una venta, pedido o turno.")
    model_name, permissions, line_model, fk = specs[kind]
    bot._require_access(profile, *permissions)
    model = _model(model_name)
    row = model.objects.filter(pk=_pk(model, arguments.get("id"))).first()
    if row is None:
        raise bot.TelegramBotError("No existe ese registro. Comprueba su ID.")
    heading = f"{kind.capitalize()} #{row.pk}"
    if kind == "turno":
        heading += f" · {row.estado}\nCajero: {_format(row.cajero.nombreusuario)} · Caja: {_format(row.puntopago.nombre)}"
        heading += f"\nInicio: {_format(row.inicio)} · Fin: {_format(row.fin)}\nValores guardados del turno (no cálculo en vivo): ventas {bot._list_money(row.ventas_total)}; diferencia {bot._list_money(row.diferencia_total)}."
        heading += "\nFacturas pagadas es informativo: no se suma otra vez a las ventas."
        fields = cols("Medio:metodo:method", "Esperado:esperado:money", "Contado:contado:money", "Diferencia:diferencia:money")
    else:
        total = row.total if kind == "venta" else row.costototal
        heading += f" · Total: {bot._list_money(total)}"
        if kind == "venta":
            heading += f"\nFecha: {_format(row.fecha)} · Cliente: {_format(row.clienteid)} · Sucursal: {_format(row.sucursalid.nombre)}"
            payments = list(_model("PagoVenta").objects.filter(ventaid=row).values("medio_pago").annotate(total=Sum("monto")).order_by("medio_pago"))
            heading += "\nPagos: " + (", ".join(f"{bot.payment_method_label(p['medio_pago'])}: {bot._list_money(p['total'])}" for p in payments[:10]) if payments else bot.payment_method_label(row.mediopago))
            if len(payments) > 10:
                heading += " (primeros 10 medios; consulta la venta en la web para ver todos)"
        else:
            heading += f"\nProveedor: {_format(row.proveedorid.nombre)} · Estado: {row.estado} · Pagado: {_format(row.monto_pagado, 'money')}"
        fields = cols("Producto:productoid__nombre", "ID producto:productoid_id", "Cantidad registrada:cantidad", "Precio unitario:preciounitario:money")
    return _page_reply("consultar_detalle_operativo", arguments, heading, _model(line_model).objects.filter(**{fk: row}).order_by("pk"), fields)


def tool_ranking(profile, arguments):
    bot = _bot()
    bot._require_access(profile, "metricas_negocio")
    args = dict(arguments)
    start, end = bot._date_range(args)
    args.update(desde=start.isoformat(), hasta=end.isoformat())
    rows = _model("DetalleVenta").objects.filter(ventaid__fecha__range=(start, end))
    heading = f"Productos vendidos · {start:%d/%m/%Y} a {end:%d/%m/%Y}"
    if args.get("sucursal"):
        branch = bot._find_branch(args["sucursal"])
        rows = rows.filter(ventaid__sucursalid=branch)
        args["sucursal"] = str(branch.pk)
        heading += f" · {_format(branch.nombre)}"
    metric = args.get("orden", "importe")
    if metric not in {"importe", "cantidad"}:
        raise bot.TelegramBotError("Ordena por importe o cantidad.")
    rows = rows.values("productoid", "productoid__nombre").annotate(
        total_cantidad=Sum("cantidad"),
        importe=Sum(F("cantidad") * F("preciounitario"), output_field=DecimalField(max_digits=22, decimal_places=2)),
    ).order_by(("" if args.get("ascendente") is True else "-") + ("total_cantidad" if metric == "cantidad" else metric), "productoid")
    total = rows.count()
    page, pages, offset = bot._list_page(args, total)
    lines = [heading, f"{total} producto(s) · página {page} de {pages}", "Importes de los renglones de venta; no descuentan descuentos globales ni reintegros. Cantidades según la unidad registrada del producto."]
    for row in rows[offset:offset + bot.LIST_PAGE_SIZE]:
        lines.append(f"• #{row['productoid']} {_format(row['productoid__nombre'])}: cantidad {row['total_cantidad']}; importe {bot._list_money(row['importe'])}")
    if not total:
        lines.append("No hay ventas con esos filtros.")
    return bot.BotReply("\n".join(lines), "ranking_productos", pagination={"page": page, "pages": pages, "arguments": bot._json_safe(args)})


def tool_views(profile, arguments):
    from mainApp.permissions import NAV_GROUPS
    bot = _bot()
    bot._require_access(profile, "home")
    query = bot._normalized_text(arguments.get("consulta") or "")
    origin = urlsplit(str(getattr(settings, "TELEGRAM_WEBHOOK_URL", "") or ""))
    base = f"{origin.scheme}://{origin.netloc}" if origin.scheme in {"https", "http"} and origin.netloc else "https://merk888.pythonanywhere.com"
    found = []

    def visit(items, parent=""):
        for item in items:
            label = f"{parent} / {item['label']}" if parent else item["label"]
            if item.get("children"):
                visit(item["children"], label)
            elif item.get("url_name") and bot.user_can_access_url_name(profile.usuario, item["url_name"]):
                searchable = bot._normalized_text(label + " " + item["url_name"])
                if all(term in searchable or (len(term) > 3 and term.endswith("s") and term[:-1] in searchable) for term in query.split()):
                    found.append((label, base + reverse(item["url_name"])))

    visit(NAV_GROUPS)
    page, pages, offset = bot._list_page(arguments, len(found))
    lines = [f"Páginas disponibles para tu usuario · {len(found)} resultado(s) · página {page} de {pages}", "Estos enlaces abren la web; no ejecutan la operación. Inicia sesión con tu cuenta."]
    lines.extend(f"• {label}\n{url}" for label, url in found[offset:offset + bot.LIST_PAGE_SIZE])
    if not found:
        lines.append("No encontré páginas accesibles con esa búsqueda.")
    return bot.BotReply("\n".join(lines), "buscar_vistas", pagination={"page": page, "pages": pages, "arguments": bot._json_safe(arguments)})


# Formularios y campos fijos: no modificar saldos, stock, cuentas, roles o claves.
EDITABLE = {
    "categoria": ("Categoria", "CategoriaForm", "EditarCategoriaForm", "agregar_categoria", "editar_categoria", "visualizar_categorias", ("nombre", "descripcion")),
    "sucursal": ("Sucursal", "SucursalForm", "SucursalEditarForm", "agregar_sucursal", "editar_sucursal", "visualizar_sucursales", ("nombre", "direccion", "telefono")),
    "cliente": ("Cliente", "ClienteForm", "EditarClienteForm", "agregar_cliente", "editar_cliente", "visualizar_clientes", ("nombre", "apellido", "numerodocumento", "telefono", "email")),
    "proveedor": ("Proveedor", "ProveedorForm", "EditarProveedorForm", "agregar_proveedor", "editar_proveedor", "visualizar_proveedores", ("nombre", "empresa", "telefono", "email", "direccion")),
    "producto": ("Producto", "ProductoForm", "ProductoEditarForm", "agregar_producto", "editar_producto", "visualizar_productos", ("nombre", "descripcion", "precio", "categoria", "codigo_de_barras", "iva", "impuesto_consumo", "icui", "ibua", "rentabilidad")),
}


def _catalog_spec(profile, entity, operation):
    bot = _bot()
    spec = EDITABLE.get(entity)
    if spec is None or operation not in {"crear", "editar"}:
        raise bot.TelegramBotError("Solo puedo crear o editar productos, categorías, clientes, proveedores y sucursales. Para otros cambios busca su página con /vistas.")
    bot._require_access(profile, spec[3] if operation == "crear" else spec[4])
    if operation == "editar":
        bot._require_access(profile, spec[5])
    return spec


def _snapshot(instance):
    # Solo modelos de EDITABLE, sin contraseñas o datos internos de otras tablas.
    return _bot()._json_safe({field.attname: getattr(instance, field.attname) for field in instance._meta.concrete_fields})


def _catalog_form(spec, operation, instance, changes):
    from mainApp import forms
    bot = _bot()
    data = model_to_dict(instance)
    data.update(changes)
    # Igual que la web: los opcionales numéricos de un producto nuevo valen cero.
    if spec[0] == "Producto" and operation == "crear":
        for key in ("iva", "impuesto_consumo", "icui", "ibua", "rentabilidad"):
            data.setdefault(key, 0)
    form = getattr(forms, spec[1] if operation == "crear" else spec[2])(data=data, instance=instance)
    valid = form.is_valid()
    if spec[0] == "Producto":
        for key in ("precio", "iva", "impuesto_consumo", "icui", "ibua", "rentabilidad"):
            value = form.cleaned_data.get(key)
            if value is not None and (not Decimal(str(value)).is_finite() or value < 0 or (key == "iva" and value > 1) or (key == "rentabilidad" and value > 100)):
                form.add_error(key, "Valor fuera de rango: usa valores no negativos, IVA de 0 a 1 y rentabilidad de 0 a 100.")
                valid = False
    if not valid:
        errors = "; ".join(f"{key}: {', '.join(messages)}" for key, messages in form.errors.items())
        raise bot.TelegramBotError("Revisa los datos antes de continuar: " + bot._list_text(errors, 1800))
    return form


def tool_prepare_catalog(profile, arguments, update=None):
    bot = _bot()
    entity, operation = arguments.get("entidad"), arguments.get("operacion")
    spec = _catalog_spec(profile, entity, operation)
    raw = arguments.get("campos")
    if not isinstance(raw, list) or not raw or len(raw) > len(spec[6]):
        raise bot.TelegramBotError("Indica los campos que quieres guardar. Usa /acciones " + entity + " para verlos.")
    changes = {}
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"campo", "valor"} or item["campo"] not in spec[6] or item["campo"] in changes:
            raise bot.TelegramBotError("La propuesta contiene campos no permitidos o repetidos.")
        if not isinstance(item["valor"], str) or len(item["valor"]) > 1000:
            raise bot.TelegramBotError("Envía cada valor como texto de máximo 1000 caracteres.")
        changes[item["campo"]] = item["valor"].strip()
    model = _model(spec[0])
    if operation == "editar":
        if arguments.get("registro_id") is not None:
            instance = model.objects.filter(pk=_pk(model, arguments["registro_id"])).first()
        elif arguments.get("registro"):
            matches = list(model.objects.filter(nombre__iexact=str(arguments["registro"]).strip()).order_by("pk")[:2])
            if len(matches) != 1:
                raise bot.TelegramBotError("No encontré un único registro con ese nombre exacto. Consulta el catálogo e indica el ID; no se preparó ningún cambio.")
            instance = matches[0]
        else:
            raise bot.TelegramBotError("Indica el ID o el nombre exacto del registro que quieres editar.")
        if instance is None:
            raise bot.TelegramBotError("No encontré el registro. Consulta primero su ID exacto.")
        before = _snapshot(instance)
    else:
        if arguments.get("registro_id"):
            raise bot.TelegramBotError("Para crear no envíes un ID existente; para cambiarlo usa editar.")
        instance, before = model(), None
    form = _catalog_form(spec, operation, instance, changes)
    lines = [f"Propuesta: {operation} {entity}" + (f" #{instance.pk} ({before['nombre']})" if operation == "editar" else " nuevo")]
    # El formulario normaliza; confirmar exactamente los valores que muestra.
    cleaned = {}
    for key in changes:
        value = form.cleaned_data[key]
        cleaned[key] = str(value.pk if hasattr(value, "pk") else (value if value is not None else ""))
        old = before.get(model._meta.get_field(key).attname) if before else None
        display = (f"{old if old is not None else '—'} → " if before else "") + cleaned[key]
        if key == "categoria":
            display += f" ({value.nombre})"
        lines.append(f"• {key}: {display}")
    if before and all(str(before.get(model._meta.get_field(key).attname) or "") == value for key, value in cleaned.items()):
        return bot.BotReply("Los valores indicados ya están guardados. No se preparó ningún cambio.", "sin_cambios")
    lines.append("\nTodavía no se ha guardado. Confirma para aplicar; la propuesta vence en 10 minutos.")
    text = "\n".join(lines)
    if len(text) > 3400:
        raise bot.TelegramBotError("La propuesta es demasiado larga. Divide el cambio en varias solicitudes para poder revisarlo completo.")
    pending = _model("TelegramAccionPendiente").objects.create(
        telegram_usuario=profile, actualizacion=update, accion="cambio_catalogo",
        argumentos={"entidad": entity, "operacion": operation, "registro_id": instance.pk if before else None, "datos": cleaned, "anterior": before},
        resumen=lines[0][:500], vence_en=timezone.now() + timedelta(minutes=bot.ACTION_TTL_MINUTES),
    )
    return bot.BotReply(text, "preparar_cambio_catalogo", reply_markup={"inline_keyboard": [[
        {"text": "Confirmar cambio", "callback_data": f"confirm:{pending.pk}"},
        {"text": "Cancelar", "callback_data": f"cancel:{pending.pk}"},
    ]]})


def confirm_catalog(profile, action):
    """Llamar con la propuesta bloqueada en una transacción del callback."""
    bot = _bot()
    args = action.argumentos
    spec = _catalog_spec(profile, args.get("entidad"), args.get("operacion"))
    try:
        # Savepoint: un error de unicidad no deja roto el atomic del callback.
        with transaction.atomic():
            model = _model(spec[0])
            instance = model()
            if args["operacion"] == "editar":
                instance = model.objects.select_for_update().filter(pk=_pk(model, args["registro_id"])).first()
                if instance is None or _snapshot(instance) != args["anterior"]:
                    raise bot.TelegramBotError("El registro cambió desde la propuesta o ya no existe. Solicita el cambio nuevamente; no sobrescribí nada.")
            if not isinstance(args.get("datos"), dict) or not args["datos"] or set(args["datos"]) - set(spec[6]):
                raise bot.TelegramBotError("La propuesta no contiene campos permitidos.")
            old_price = getattr(instance, "precio", None)
            old_previous = getattr(instance, "precio_anterior", None)
            form = _catalog_form(spec, args["operacion"], instance, args["datos"])
            obj = form.save(commit=False)
            if args["entidad"] == "producto" and args["operacion"] == "editar":
                obj.precio_anterior = old_price if obj.precio != old_price else old_previous
            obj.save()
            form.save_m2m()
            action.estado = "CONFIRMADA"
            action.resuelto_en = timezone.now()
            action.save(update_fields=["estado", "resuelto_en"])
            bot._audit(profile, "confirmar_cambio_catalogo", args, detail=f"{spec[0]} #{obj.pk}")
        return f"Cambio guardado: {args['entidad']} #{obj.pk}. {action.resumen}."
    except (bot.TelegramBotError, IntegrityError) as exc:
        message = str(exc) if isinstance(exc, bot.TelegramBotError) else "Los datos entran en conflicto con otro registro. Solicita una nueva propuesta."
        action.estado = "ERROR"
        action.resuelto_en = timezone.now()
        action.save(update_fields=["estado", "resuelto_en"])
        bot._audit(profile, "confirmar_cambio_catalogo", args, successful=False, detail=message)
        return "No se guardó el cambio. " + message


def tool_capabilities(profile, arguments):
    bot = _bot()
    bot._require_access(profile, "home")
    entity = arguments.get("entidad")
    if entity:
        from mainApp import forms
        spec = EDITABLE.get(entity)
        if not spec:
            raise bot.TelegramBotError("Entidades editables: " + ", ".join(EDITABLE))
        bot._require_access(profile, spec[3], spec[4])
        form = getattr(forms, spec[1])()
        required = [key for key in spec[6] if form.fields[key].required]
        return "\n".join([f"Campos de {entity}: {', '.join(spec[6])}", "Obligatorios al crear: " + ", ".join(required), "Al editar, indica el ID o nombre exacto único y solo los campos que quieres cambiar. Categoría usa el ID existente; IVA se expresa de 0 a 1. Siempre se solicita confirmación."])
    readable = [key.replace("_", " ") for key, spec in RESOURCES.items() if bot.user_can_access_url_name(profile.usuario, spec.permission)]
    writable = []
    for key, spec in EDITABLE.items():
        verbs = []
        if bot.user_can_access_url_name(profile.usuario, spec[3]):
            verbs.append("crear")
        if bot.user_can_access_url_name(profile.usuario, spec[4]) and bot.user_can_access_url_name(profile.usuario, spec[5]):
            verbs.append("editar")
        if verbs:
            writable.append(f"{key} ({'/'.join(verbs)})")
    from mainApp.permissions import user_can_change_sale
    if user_can_change_sale(profile.usuario):
        writable.append("devolver productos de una venta y registrar su reintegro")
    return "\n".join([
        "Consultas habilitadas para tu usuario: " + (", ".join(readable) or "ninguna"),
        "También están disponibles los totales, pagos, balance, empleados y turnos según tus permisos; detalle de venta/pedido/turno y ranking de productos.",
        "Cambios con confirmación: registrar pagos" + ("; " + ", ".join(writable) if writable else "") + ".",
        "Usa /acciones producto (o categoría, cliente, proveedor, sucursal) para consultar los campos.",
        "Puedes solicitar devoluciones por texto/audio o /devolver VENTA PRODUCTO:CANTIDAD MEDIO, con permiso y confirmación. Efectivo es el medio predeterminado; no realiza transferencias bancarias.",
        "Usa /vistas PALABRA para buscar páginas. Cierres, facturación, ajustes manuales de stock, eliminación y configuración sensible se realizan en la web, no automáticamente desde el chat.",
        "Puedes escribir o enviar audio. Pide solo el total, una lista, filtros, fechas o la siguiente página. No muestro contraseñas ni claves API.",
    ])


TOOL_FUNCTIONS = {
    "consultar_registros": tool_records,
    "consultar_detalle_operativo": tool_detail,
    "ranking_productos": tool_ranking,
    "buscar_vistas": tool_views,
    "preparar_cambio_catalogo": tool_prepare_catalog,
    "consultar_capacidades": tool_capabilities,
    "preparar_devolucion_venta": tool_prepare_return,
}

TOOL_DEFINITIONS = [
    RETURN_TOOL_DEFINITION,
    {"name": "consultar_registros", "description": "Lista, busca o cuenta registros reales de los catálogos y operaciones. Eventos: hoy salvo ID exacto o fechas explícitas. No modifica datos.", "parameters": {"type": "OBJECT", "properties": {
        "recurso": {"type": "STRING", "enum": list(RESOURCES)}, "consulta": {"type": "STRING"},
        "registro_id": {"type": "STRING"}, "sucursal": {"type": "STRING"}, "desde": {"type": "STRING"}, "hasta": {"type": "STRING"},
        "categoria": {"type": "STRING", "description": "ID o nombre exacto de categoría para productos, inventario o precios de proveedor."},
        "estado": {"type": "STRING", "description": "Estado del pedido/devolución o activo/inactivo para usuarios, métodos y funcionalidades."},
        "sin_ventas": {"type": "BOOLEAN", "description": "Solo productos sin ninguna venta en el historial disponible."},
        "stock_max": {"type": "INTEGER", "description": "Solo inventario: cantidad máxima inclusive; 0 para agotados."},
        "vinculado": {"type": "BOOLEAN", "description": "Solo Nequi: con venta vinculada o sin vincular. Solo ingresos."},
        "solo_total": {"type": "BOOLEAN", "description": "true si pregunta cuántos/cuánto: omite lista."},
        "incluir_contacto": {"type": "BOOLEAN", "description": "Solo si pide explícitamente teléfono/correo de clientes o proveedores."},
        "pagina": {"type": "INTEGER"},
    }, "required": ["recurso"]}},
    {"name": "consultar_detalle_operativo", "description": "Detalle de una venta o pedido con productos, o valores guardados de turno con facturas pagadas. Requiere ID exacto.", "parameters": {"type": "OBJECT", "properties": {"tipo": {"type": "STRING", "enum": ["venta", "pedido", "turno"]}, "id": {"type": "INTEGER"}, "pagina": {"type": "INTEGER"}}, "required": ["tipo", "id"]}},
    {"name": "ranking_productos", "description": "Productos más/menos vendidos por cantidad o importe de renglones en un intervalo; no incluye productos sin ventas.", "parameters": {"type": "OBJECT", "properties": {"desde": {"type": "STRING"}, "hasta": {"type": "STRING"}, "sucursal": {"type": "STRING"}, "orden": {"type": "STRING", "enum": ["cantidad", "importe"]}, "ascendente": {"type": "BOOLEAN"}, "pagina": {"type": "INTEGER"}}}},
    {"name": "buscar_vistas", "description": "Devuelve enlaces a las páginas permitidas del sistema. Usar para procesos no ejecutables por chat; NO ejecuta operaciones.", "parameters": {"type": "OBJECT", "properties": {"consulta": {"type": "STRING"}, "pagina": {"type": "INTEGER"}}}},
    {"name": "preparar_cambio_catalogo", "description": "Prepara crear/editar un registro de catálogo. No guarda hasta confirmación. Usa consultar_capacidades para conocer campos; no inventes datos obligatorios. Editar exige ID o nombre exacto único.", "parameters": {"type": "OBJECT", "properties": {
        "entidad": {"type": "STRING", "enum": list(EDITABLE)}, "operacion": {"type": "STRING", "enum": ["crear", "editar"]}, "registro_id": {"type": "INTEGER"},
        "registro": {"type": "STRING", "description": "Nombre exacto del registro existente a editar si no se conoce el ID. Si hay ambigüedad no se prepara el cambio."},
        "campos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"campo": {"type": "STRING", "enum": sorted({field for spec in EDITABLE.values() for field in spec[6]})}, "valor": {"type": "STRING", "description": "Valor exacto solicitado; categoría usa ID, decimales con punto, IVA 0 a 1."}}, "required": ["campo", "valor"]}},
    }, "required": ["entidad", "operacion", "campos"]}},
    {"name": "consultar_capacidades", "description": "Muestra las capacidades permitidas o los campos y requisitos de una entidad editable.", "parameters": {"type": "OBJECT", "properties": {"entidad": {"type": "STRING", "enum": list(EDITABLE)}}}},
]


def validate_arguments(tool_name, arguments):
    """Validar también en servidor: la IA no es una frontera de seguridad."""
    schema = next(item["parameters"] for item in TOOL_DEFINITIONS if item["name"] == tool_name)

    def check(value, rule, path):
        kind = rule["type"]
        valid = {
            "OBJECT": isinstance(value, dict),
            "ARRAY": isinstance(value, list),
            "STRING": isinstance(value, str),
            "INTEGER": isinstance(value, int) and not isinstance(value, bool),
            "BOOLEAN": isinstance(value, bool),
        }.get(kind, False)
        if not valid or ("enum" in rule and value not in rule["enum"]):
            raise _bot().TelegramBotError(f"Valor no permitido en {path}. Revisa los datos de la solicitud.")
        if kind == "OBJECT":
            properties = rule.get("properties", {})
            if set(value) - set(properties) or set(rule.get("required", [])) - set(value):
                raise _bot().TelegramBotError(f"Hay campos faltantes o no permitidos en {path}. Consulta /acciones para ver las opciones.")
            for key, item in value.items():
                check(item, properties[key], f"{path}.{key}")
        elif kind == "ARRAY":
            if len(value) > 30:
                raise _bot().TelegramBotError("Demasiados campos en una sola solicitud.")
            for item in value:
                check(item, rule["items"], path)

    check(arguments, schema, "solicitud")
