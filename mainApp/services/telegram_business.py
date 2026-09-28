"""Nuevas capacidades con propuestas verificables y confirmación idempotente."""
import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from .business_operations import BusinessOperationError, create_supplier_order, update_inventory_item, validate_supplier_order
from .telegram_search import name_key, resolve_name


def _bot():
    from . import telegram_bot
    return telegram_bot


ALIASES = {
    "producto": ("Producto", "visualizar_productos", ("nombre",)),
    "proveedor": ("Proveedor", "visualizar_proveedores", ("nombre", "empresa")),
    "cliente": ("Cliente", "visualizar_clientes", ("nombre", "apellido")),
    "empleado": ("Empleado", "visualizar_empleados", ("nombre", "apellido")),
    "sucursal": ("Sucursal", "visualizar_sucursales", ("nombre",)),
    "categoria": ("Categoria", "visualizar_categorias", ("nombre",)),
}
FOLLOWUPS = {"resumen_diario": "Resumen diario", "diferencias_caja": "Diferencias de caja", "stock_bajo": "Inventario agotado"}


def model(name):
    from django.apps import apps
    return apps.get_model("mainApp", name)


def active_profile(profile):
    if not profile.activo or not profile.usuario.is_active:
        raise PermissionDenied("La cuenta vinculada está inactiva.")


def require_business_access(profile, kind, data):
    active_profile(profile)
    bot = _bot()
    if kind == "inventario":
        bot._require_access(profile, "editar_inventario")
        bot._require_access(profile, "visualizar_inventarios")
    elif kind == "pedido":
        bot._require_access(profile, "agregar_pedido")
        bot._require_access(profile, "visualizar_productos_precios_proveedores")
    elif kind == "alias":
        spec = ALIASES.get(data.get("entidad"))
        if not spec:
            raise bot.TelegramBotError("No puedo guardar alias para ese tipo de registro.")
        bot._require_access(profile, spec[1])
    elif kind == "seguimiento":
        permissions = {"resumen_diario": "metricas_negocio", "diferencias_caja": "turnos_caja_dashboard", "stock_bajo": "visualizar_inventarios"}
        if data.get("tipo") not in permissions:
            raise bot.TelegramBotError("Ese seguimiento no está disponible.")
        bot._require_access(profile, permissions[data["tipo"]])
    else:
        raise bot.TelegramBotError("Operación no disponible.")


def proposal(profile, kind, data, text, update):
    bot = _bot()
    require_business_access(profile, kind, data)
    if len(text) > 3100:
        raise bot.TelegramBotError("Son demasiados datos para revisar juntos. Divide la solicitud.")
    action = model("TelegramAccionPendiente").objects.create(
        telegram_usuario=profile, actualizacion=update, accion="operacion_negocio",
        argumentos={"tipo": kind, "datos": bot._json_safe(data)}, resumen=text.splitlines()[0][:500],
        vence_en=timezone.now() + timedelta(minutes=bot.ACTION_TTL_MINUTES),
    )
    return bot.BotReply(text + "\n\nTodavía no guardé nada. Revisa los datos y confirma; vence en 10 minutos.",
                       "preparar_operacion_negocio", proposal_id=str(action.pk), reply_markup={"inline_keyboard": [[
                           {"text": "Confirmar", "callback_data": f"confirm:{action.pk}"},
                           {"text": "Cancelar", "callback_data": f"cancel:{action.pk}"},
                       ]]})


def tool_prepare_inventory(profile, args, update=None):
    bot = _bot()
    require_business_access(profile, "inventario", args)
    product = resolve_name(model("Producto").objects.all(), args["producto"], entity="producto")
    branch = bot._find_branch(args["sucursal"])
    reason = args["motivo"].strip()
    if not 5 <= len(reason) <= 300:
        raise bot.TelegramClarification("Dime el motivo del movimiento de inventario (entre 5 y 300 caracteres).")
    quantity = args["cantidad"]
    if not -2147483648 <= quantity <= 2147483647:
        raise bot.TelegramBotError("La cantidad está fuera del rango permitido.")
    before = model("Inventario").objects.filter(productoid=product, sucursalid=branch).values_list("cantidad", flat=True).first() or 0
    mode = args["modo"]
    if mode == "sumar" and before > 9000:
        raise bot.TelegramBotError("Primero cuenta este producto; el sistema bloquea el surtido cuando el stock supera 9000.")
    after = before + quantity if mode == "sumar" else quantity
    if not -2147483648 <= after <= 2147483647:
        raise bot.TelegramBotError("La cantidad resultante está fuera del rango permitido.")
    data = {"producto_id": product.pk, "sucursal_id": branch.pk, "cantidad": quantity, "modo": mode, "anterior": before, "motivo": reason}
    text = f"Ajuste de inventario · {branch.nombre}\n{product.nombre} · ID {product.pk}\nExistencias: {before} → {after}\nOperación: {'sumar ' + str(quantity) if mode == 'sumar' else 'fijar conteo exacto'}\nMotivo: {reason}\nLas cantidades usan la unidad del producto; los productos por peso se registran en gramos."
    return proposal(profile, "inventario", data, text, update)


def tool_restock(profile, args):
    bot = _bot()
    bot._require_access(profile, "visualizar_inventarios")
    bot._require_access(profile, "visualizar_productos_precios_proveedores")
    branch = bot._find_branch(args["sucursal"])
    maximum = args.get("stock_max", 0)
    rows = model("Inventario").objects.filter(sucursalid=branch, cantidad__lte=maximum).select_related("productoid").order_by("cantidad", "productoid_id")
    total = rows.count()
    lines = [f"En {branch.nombre} encontré {total} productos con existencias de {maximum} o menos."]
    references = []
    for item in rows[:10]:
        references.append({"entidad": "productos", "id": item.productoid_id})
        lines.append(f"• #{item.productoid_id} {item.productoid.nombre} · stock {item.cantidad}")
        prices = model("PreciosProveedor").objects.filter(productoid_id=item.productoid_id, precio__gt=0).select_related("proveedorid").order_by("precio", "pk")[:3]
        offers = [f"{p.proveedorid.nombre} (ID {p.proveedorid_id}): {bot._list_money(p.precio)}" for p in prices]
        lines.append("  " + ("; ".join(offers) or "Sin precio de proveedor registrado."))
    if total > 10:
        lines.append("Mostré los primeros 10. Pide otra consulta con filtros para revisar el resto.")
    lines.append("Son precios registrados, no cotizaciones en vivo. Para preparar un pedido dime proveedor, productos y cantidades; no los decido por ti.")
    return bot.BotReply("\n".join(lines), "planificar_reabastecimiento", references=references)


def tool_prepare_order(profile, args, update=None):
    bot = _bot()
    require_business_access(profile, "pedido", args)
    provider = resolve_name(model("Proveedor").objects.all(), args["proveedor"], ("nombre", "empresa"), entity="proveedor")
    branch = bot._find_branch(args["sucursal"])
    if not 1 <= len(args["productos"]) <= 10:
        raise bot.TelegramBotError("Prepara de 1 a 10 productos por pedido para revisar la propuesta completa.")
    details, quotes, text = [], [], [f"Pedido para {provider.nombre} · sucursal {branch.nombre}"]
    for item in args["productos"]:
        product = resolve_name(model("Producto").objects.all(), item["producto"], entity="producto")
        price_row = model("PreciosProveedor").objects.filter(productoid=product, proveedorid=provider).order_by("pk").first()
        if price_row is None:
            raise bot.TelegramBotError(f"{provider.nombre} no tiene precio registrado para {product.nombre}.")
        price = Decimal(str(item.get("precio_unitario", price_row.precio)))
        details.append({"productoid": product.pk, "cantidad": item["cantidad"], "precio_unitario": str(price)})
        if "precio_unitario" not in item:
            quotes.append({"id": price_row.pk, "precio": str(price_row.precio)})
        text.append(f"• #{product.pk} {product.nombre}: {item['cantidad']} × {bot._list_money(price)}")
    data = {"proveedor": provider.pk, "sucursal": branch.pk, "fechaestimadaentrega": args.get("fecha_entrega", ""),
            "comentario": args.get("comentario", ""), "detalles": json.dumps(details), "cotizaciones": quotes}
    try:
        _, _, total = validate_supplier_order(data)
    except BusinessOperationError as exc:
        raise bot.TelegramBotError(str(exc)) from None
    text.extend([f"Total: {bot._list_money(total)}", f"Entrega estimada: {args.get('fecha_entrega') or 'Sin fecha'}",
                 f"Comentario: {args.get('comentario') or 'Sin comentario'}", "Quedará En espera. No registra recepción, pagos ni movimientos de caja."])
    return proposal(profile, "pedido", data, "\n".join(text), update)


def tool_prepare_alias(profile, args, update=None):
    bot = _bot()
    require_business_access(profile, "alias", args)
    alias, key = args["alias"].strip(), name_key(args["alias"])
    if not key or key.isdigit() or len(alias) > 100 or len(key) > 100:
        raise bot.TelegramBotError("El alias debe ser un nombre de hasta 100 caracteres, no un ID.")
    spec = ALIASES[args["entidad"]]
    obj = model(spec[0]).objects.filter(pk=args["registro_id"]).first()
    if obj is None:
        raise bot.TelegramBotError("No encontré ese registro. Revisa su ID.")
    # Un alias no puede secuestrar el nombre exacto de otro registro real.
    if model(spec[0]).objects.filter(**{spec[2][0] + "__iexact": alias}).exclude(pk=obj.pk).exists():
        raise bot.TelegramBotError("Ese nombre ya identifica otro registro; elige un alias distinto.")
    label = " ".join(str(getattr(obj, field, "") or "") for field in spec[2]).strip()
    data = {"entidad": args["entidad"], "alias": alias, "clave": key, "registro_id": obj.pk}
    return proposal(profile, "alias", data, f"Recordar nombre para tu cuenta\nCuando digas «{alias}» como {args['entidad']}, buscaré #{obj.pk}: {label}.\nNo renombra el registro ni cambia las búsquedas de otros usuarios.", update)


def tool_aliases(profile, args):
    bot = _bot()
    active_profile(profile)
    rows = model("TelegramAlias").objects.filter(telegram_usuario=profile).order_by("entidad", "nombre")
    lines = []
    for alias in rows[:50]:
        spec = ALIASES.get(alias.entidad)
        if spec and bot.user_can_access_url_name(profile.usuario, spec[1]):
            lines.append(f"• {alias.nombre} → {alias.entidad} #{alias.registro_id}")
    return "Nombres que recuerdo para tu cuenta:\n" + "\n".join(lines) if lines else "Todavía no tienes alias disponibles."


def tool_prepare_followup(profile, args, update=None):
    bot = _bot()
    require_business_access(profile, "seguimiento", args)
    try:
        hour = datetime.strptime(args["hora"], "%H:%M").time()
    except ValueError:
        raise bot.TelegramClarification("¿A qué hora de Colombia? Usa HH:MM, por ejemplo 21:00.") from None
    data = {"tipo": args["tipo"], "hora": hour.strftime("%H:%M"), "activo": args.get("activo", True)}
    text = f"{'Activar' if data['activo'] else 'Pausar'} seguimiento: {FOLLOWUPS[data['tipo']]}\nTodos los días a las {data['hora']} (Colombia), solo en tu chat."
    if data["tipo"] != "resumen_diario":
        text += "\nSolo avisa cuando hay registros que revisar; máximo un informe por día."
    text += "\nRequiere el procesador de Telegram ejecutándose con la opción --followups. No realizará cambios contables."
    return proposal(profile, "seguimiento", data, text, update)


def tool_followups(profile, args):
    active_profile(profile)
    rows = model("TelegramSeguimiento").objects.filter(telegram_usuario=profile).order_by("pk")
    lines = []
    for rule in rows:
        lines.append(f"• {FOLLOWUPS.get(rule.tipo, rule.tipo)} · {rule.hora:%H:%M} Colombia · {'activo' if rule.activo else 'pausado'}")
        last = rule.envios.order_by("-fecha", "-pk").first()
        if last:
            states = {"RESERVADO": "envío reservado, aún sin confirmación", "ENVIADO": "enviado", "OMITIDO": "sin envío", "ERROR_INCIERTO": "envío no confirmado; revisa el procesador"}
            lines.append(f"  Última revisión {last.fecha:%d/%m}: {states.get(last.estado, 'pendiente')}. {last.detalle}")
        else:
            lines.append("  Sin revisiones todavía; requiere el procesador con --followups.")
    return "Tus seguimientos:\n" + "\n".join(lines) if lines else "No tienes seguimientos programados. Dime qué informe y a qué hora quieres recibirlo."


def confirm_business(profile, action):
    bot = _bot()
    kind, data = action.argumentos["tipo"], action.argumentos["datos"]
    require_business_access(profile, kind, data)
    try:
        with transaction.atomic():
            if kind == "inventario":
                result = update_inventory_item(branch_id=data["sucursal_id"], product_id=data["producto_id"],
                    quantity=data["cantidad"], mode=data["modo"], expected=data["anterior"])
                message = f"Listo, {result['product_name']} quedó con {result['new_cantidad']} en la sucursal indicada."
            elif kind == "pedido":
                for quote in data["cotizaciones"]:
                    current = model("PreciosProveedor").objects.select_for_update().filter(pk=quote["id"]).first()
                    if current is None or current.precio != Decimal(quote["precio"]):
                        raise BusinessOperationError("Cambió un precio del proveedor. Pide una nueva propuesta antes de guardar el pedido.")
                order = create_supplier_order(data)
                message = f"Listo, creé el pedido #{order.pk} por {bot._list_money(order.costototal)}. Quedó En espera; no se registró ningún pago."
            elif kind == "alias":
                spec = ALIASES[data["entidad"]]
                if not model(spec[0]).objects.filter(pk=data["registro_id"]).exists():
                    raise BusinessOperationError("El registro ya no existe.")
                if model(spec[0]).objects.filter(**{spec[2][0] + "__iexact": data["alias"]}).exclude(pk=data["registro_id"]).exists():
                    raise BusinessOperationError("Ahora ese nombre identifica otro registro. Elige un alias distinto.")
                model("TelegramAlias").objects.update_or_create(telegram_usuario=profile, entidad=data["entidad"], clave=data["clave"],
                    defaults={"nombre": data["alias"], "registro_id": data["registro_id"]})
                message = f"Listo, recordaré «{data['alias']}» para ese {data['entidad']} en tu cuenta."
            else:
                model("TelegramSeguimiento").objects.update_or_create(telegram_usuario=profile, tipo=data["tipo"],
                    defaults={"hora": datetime.strptime(data["hora"], "%H:%M").time(), "activo": data["activo"]})
                message = f"Seguimiento {'activado' if data['activo'] else 'pausado'}: {FOLLOWUPS[data['tipo']]}. Requiere el procesador con --followups en ejecución."
            action.estado = "CONFIRMADA"
            action.resuelto_en = timezone.now()
            action.save(update_fields=["estado", "resuelto_en"])
            bot._audit(profile, "confirmar_operacion_negocio", action.argumentos, detail=message)
        return message
    except (BusinessOperationError, IntegrityError) as exc:
        message = str(exc) if isinstance(exc, BusinessOperationError) else "Hubo un conflicto con otro cambio. Pide una nueva propuesta."
        action.estado = "ERROR"
        action.resuelto_en = timezone.now()
        action.save(update_fields=["estado", "resuelto_en"])
        bot._audit(profile, "confirmar_operacion_negocio", action.argumentos, successful=False, detail=message)
        return "No guardé cambios. " + message


def definition(name, description, properties, required=()):
    return {"name": name, "description": description, "parameters": {"type": "OBJECT", "properties": properties, "required": list(required)}}


TEXT = {"type": "STRING"}
TOOL_DEFINITIONS = [
    definition("planificar_reabastecimiento", "Investiga hasta 10 productos agotados/bajos de una sucursal y sus precios de proveedor. No inventa cantidades ni crea pedidos.", {"sucursal": TEXT, "stock_max": {"type": "INTEGER"}}, ["sucursal"]),
    definition("preparar_movimiento_inventario", "Propone sumar existencias o fijar un conteo exacto; admite negativos, requiere motivo y confirmación. Cantidades enteras; por peso son gramos. No usar para simular una venta o recepción de pedido.", {"producto": TEXT, "sucursal": TEXT, "cantidad": {"type": "INTEGER"}, "modo": {"type": "STRING", "enum": ["sumar", "fijar"]}, "motivo": TEXT}, ["producto", "sucursal", "cantidad", "modo", "motivo"]),
    definition("preparar_pedido_proveedor", "Prepara pedido En espera de un proveedor y sucursal. Requiere cantidades explícitas; usa sus precios registrados salvo precio explícito. No recibe mercancía ni paga. Confirmación obligatoria.", {"proveedor": TEXT, "sucursal": TEXT, "fecha_entrega": TEXT, "comentario": TEXT, "productos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {"producto": TEXT, "cantidad": {"type": "INTEGER"}, "precio_unitario": {"type": "NUMBER"}}, "required": ["producto", "cantidad"]}}}, ["proveedor", "sucursal", "productos"]),
    definition("preparar_alias", "Recuerda un nombre alternativo SOLO cuando lo pide expresamente el usuario. Exige registro_id real y confirmación; no renombra el catálogo.", {"entidad": {"type": "STRING", "enum": list(ALIASES)}, "alias": TEXT, "registro_id": {"type": "INTEGER"}}, ["entidad", "alias", "registro_id"]),
    definition("consultar_aliases", "Lista los nombres alternativos de la propia cuenta.", {}),
    definition("preparar_seguimiento", "Propone activar o pausar un aviso DIARIO solicitado explícitamente por el usuario. Preguntar hora de Colombia si falta. Nunca configurar sin petición y confirmación.", {"tipo": {"type": "STRING", "enum": list(FOLLOWUPS)}, "hora": TEXT, "activo": {"type": "BOOLEAN"}}, ["tipo", "hora"]),
    definition("consultar_seguimientos", "Lista seguimientos diarios activos o pausados de la propia cuenta.", {}),
]
TOOL_FUNCTIONS = {"planificar_reabastecimiento": tool_restock, "preparar_movimiento_inventario": tool_prepare_inventory,
    "preparar_pedido_proveedor": tool_prepare_order, "preparar_alias": tool_prepare_alias, "consultar_aliases": tool_aliases,
    "preparar_seguimiento": tool_prepare_followup, "consultar_seguimientos": tool_followups}
PREPARATION_TOOLS = {"preparar_movimiento_inventario", "preparar_pedido_proveedor", "preparar_alias", "preparar_seguimiento"}
