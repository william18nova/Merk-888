"""Devoluciones de ventas por Telegram con confirmación y dominio compartido."""

import re
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection, transaction
from django.db.models import Sum
from django.utils import timezone

from mainApp.models import (
    CambioDevolucion, DetalleVenta, MetodoPago, PagoVenta, ReintegroVenta,
    TelegramAccionPendiente, Usuario, Venta,
)
from mainApp.permissions import user_can_change_sale
from .feature_flags import TURN_REQUIRED_FEATURE, is_feature_enabled, locked_feature_enabled
from .telegram_search import choose_match, rank_candidates
from .payment_methods import (
    CASH_PAYMENT_CODE, INTERNAL_PAYMENT_CODES, normalize_payment_method_code, payment_method_label,
    payment_method_options, payment_method_table_ready,
)


MAX_RETURN_LINES = 20


def _bot():
    from . import telegram_bot
    return telegram_bot


def _require_permission(profile):
    bot = _bot()
    bot._require_access(profile, "ver_venta")
    # No reutilizar permisos/cuenta en memoria de una propuesta anterior.
    user = Usuario.objects.select_related("rolid").filter(pk=profile.usuario_id).first()
    if not profile.activo or user is None or not user_can_change_sale(user):
        raise PermissionDenied("No tienes permiso para registrar cambios/devoluciones de ventas. El rol Cajero solo puede consultar e imprimir.")
    return user


def _positive_int(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= 2147483647:
        raise _bot().TelegramBotError(f"{label} debe ser un número entero mayor que cero.")
    return value


def _load_sale(sale_id):
    sale_id = _positive_int(sale_id, "El ID de venta")
    # Igual que la web: serializar modificaciones de una venta, incluidas las
    # devoluciones hechas desde otra pestaña o por otro operador.
    sale = Venta.objects.select_for_update().filter(pk=sale_id).first()
    if sale is None:
        raise _bot().TelegramBotError("No existe esa venta. Comprueba su ID.")
    if ReintegroVenta._meta.db_table not in connection.introspection.table_names():
        raise _bot().TelegramBotError("Las devoluciones requieren aplicar la migración 0021 antes de continuar.")
    details = list(DetalleVenta.objects.select_for_update().filter(ventaid=sale).order_by("pk").prefetch_related("productoid"))
    return sale, details


def _select_lines(details, items):
    bot = _bot()
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_RETURN_LINES:
        raise bot.TelegramBotError(f"Indica entre 1 y {MAX_RETURN_LINES} productos y la cantidad de cada uno.")
    chosen, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise bot.TelegramBotError("Indica el producto y su cantidad a devolver.")
        quantity = _positive_int(item.get("cantidad"), "La cantidad")
        identifiers = [key for key in ("detalle_id", "producto_id", "producto") if item.get(key) is not None]
        if len(identifiers) != 1 or set(item) - {"detalle_id", "producto_id", "producto", "cantidad"}:
            raise bot.TelegramBotError("Identifica cada renglón por ID de producto, ID de detalle o nombre, no por varios a la vez.")
        key = identifiers[0]
        if key == "producto":
            available = {detail.productoid_id: detail.productoid.nombre for detail in details if detail.cantidad > 0}
            candidates = rank_candidates(item[key], ((pk, name, ()) for pk, name in available.items()))
            if candidates:
                selected = choose_match(item[key], candidates, entity="un producto de esta venta")
                matches = [detail for detail in details if detail.cantidad > 0 and detail.productoid_id == selected.pk]
            else:
                matches = []
        else:
            value = _positive_int(item[key], "El identificador del producto/detalle")
            matches = [detail for detail in details if detail.cantidad > 0 and (detail.pk if key == "detalle_id" else detail.productoid_id) == value]
        if not matches:
            raise bot.TelegramBotError("Uno de los productos no pertenece a esta venta o ya no tiene cantidad disponible. Consulta el detalle de la venta.")
        if len(matches) != 1:
            options = ", ".join(f"detalle {row.pk}: {row.cantidad} a {bot._list_money(row.preciounitario)}" for row in matches[:5])
            raise bot.TelegramBotError(f"Ese producto aparece en varios renglones. Indica el ID de detalle: {options}.")
        detail = matches[0]
        if detail.pk in seen:
            raise bot.TelegramBotError("No repitas un mismo producto/detalle en la solicitud; indica su cantidad total una sola vez.")
        if quantity > detail.cantidad:
            raise bot.TelegramBotError(f"El producto {detail.productoid_id} solo tiene {detail.cantidad} disponible(s) para devolución; solicitaste {quantity}.")
        seen.add(detail.pk)
        chosen.append({"detalle": detail, "cantidad": quantity})
    return sorted(chosen, key=lambda item: item["detalle"].pk)


def _method(raw, *, lock=False):
    bot = _bot()
    code = bot._resolve_payment_method(raw if raw is not None else CASH_PAYMENT_CODE, active_only=True)
    if lock and payment_method_table_ready():
        # La configuración web bloquea estas mismas filas al retirarlas.
        list(MetodoPago.objects.select_for_update().filter(codigo=code))
    options = {row["code"]: row for row in payment_method_options(active_only=True) if row["active"]}
    # Mantener el contrato de efectivo predeterminado del dominio.
    if code != CASH_PAYMENT_CODE and code not in options:
        raise bot.TelegramBotError("El medio de reintegro no está activo o no es válido. Disponibles: " + ", ".join(["Efectivo"] + [row["label"] for key, row in options.items() if key != CASH_PAYMENT_CODE]) + ". No se admite un reintegro mixto en esta solicitud.")
    return code, payment_method_label(code)


def _original_payments(sale, *, lock=False):
    """Conservar el ingreso bruto: los cierres restan el reintegro por separado.

    La página web materializa venta_pagos en ventas históricas de un solo medio
    antes de devolver. Sin este paso el cierre usaría Venta.total ya reducido
    como ingreso, y descontaría el reintegro por segunda vez.
    """
    rows = PagoVenta.objects.filter(ventaid=sale).order_by("pk")
    if lock:
        rows = rows.select_for_update()
    rows = list(rows)
    returned = ReintegroVenta.objects.filter(venta=sale).aggregate(total=Sum("monto"))["total"] or Decimal("0")
    originally_paid = (sale.total + returned).quantize(Decimal("0.01"))
    snapshot = {
        "total_cobrado": str(originally_paid),
        "pagos": [{"id": row.pk, "medio_pago": row.medio_pago, "monto": str(row.monto)} for row in rows],
        "reconstruir": None,
    }
    if rows:
        if any(row.monto < 0 or normalize_payment_method_code(row.medio_pago) in INTERNAL_PAYMENT_CODES or not normalize_payment_method_code(row.medio_pago) for row in rows) or sum((row.monto for row in rows), Decimal("0")) != originally_paid:
            raise _bot().TelegramBotError("Los pagos históricos no coinciden con lo cobrado de esta venta. Revisa sus pagos en la página web antes de devolver; no los modificaré automáticamente.")
    elif originally_paid > 0:
        code = normalize_payment_method_code(sale.mediopago)
        if not code or code in INTERNAL_PAYMENT_CODES:
            raise _bot().TelegramBotError("Esta venta no tiene una distribución de pagos original recuperable. Corrige los pagos en la página web antes de devolver.")
        snapshot["reconstruir"] = {"medio_pago": code, "monto": str(originally_paid)}
    return snapshot


def _context(sale, details, selected, method, *, confirming=False):
    bot = _bot()
    total = CambioDevolucion.calcular_total_devolucion(sale, selected)
    required = locked_feature_enabled(TURN_REQUIRED_FEATURE) if confirming else is_feature_enabled(TURN_REQUIRED_FEATURE, fresh=True)
    code, label = _method(method, lock=confirming)
    payments = _original_payments(sale, lock=confirming)
    turn = None
    if required and total > 0:
        turn = CambioDevolucion._turno_abierto_para_venta_locked(sale)
        if turn is None:
            raise bot.TelegramBotError("No hay un turno activo en el punto de pago de esta venta para registrar el reintegro. Abre el turno en la web y solicita nuevamente la devolución.")
    # Se incluyen todos los renglones: el descuento prorrateado depende también
    # de productos que no se están devolviendo en esta solicitud.
    snapshot = {
        "venta": {
            "id": sale.pk, "total": str(sale.total), "medio_pago": sale.mediopago,
            "sucursal": sale.sucursalid_id, "punto_pago": sale.puntopagoid_id,
            "cliente": sale.clienteid_id, "empleado": sale.empleadoid_id,
            "fecha": str(sale.fecha), "hora": str(sale.hora),
            "sucursal_nombre": sale.sucursalid.nombre, "punto_pago_nombre": sale.puntopagoid.nombre,
        },
        "detalles": [{"id": row.pk, "producto_id": row.productoid_id, "nombre": row.productoid.nombre, "cantidad": row.cantidad, "precio": str(row.preciounitario)} for row in details],
        "turno_requerido": required,
        "turno": {"id": turn.pk, "cajero_id": turn.cajero_id, "cajero_nombre": turn.cajero.nombreusuario, "estado": turn.estado} if turn else None,
        "medio": {"codigo": code, "nombre": label},
        "total_reintegro": str(total),
        "pagos_originales": payments,
    }
    return total, turn, snapshot


def tool_prepare_return(profile, arguments, update=None):
    bot = _bot()
    _require_permission(profile)
    with transaction.atomic():
        sale, details = _load_sale(arguments.get("venta_id"))
        selected = _select_lines(details, arguments.get("productos"))
        total, turn, snapshot = _context(sale, details, selected, arguments.get("medio_pago"))
        lines = [
            f"Revisa esta devolución de la venta #{sale.pk}:",
            f"Sucursal: {bot._list_text(sale.sucursalid.nombre, 80)} · Caja: {bot._list_text(sale.puntopagoid.nombre, 80)}",
        ]
        for item in selected:
            detail = item["detalle"]
            lines.append(f"• Producto #{detail.productoid_id} {bot._list_text(detail.productoid.nombre, 80)} · detalle #{detail.pk}: devolver {item['cantidad']} de {detail.cantidad} disponible(s).")
        lines.extend([
            f"\nTotal a reintegrar: {bot._list_money(total)} · Medio: {snapshot['medio']['nombre']}",
            "Este valor ya tiene en cuenta lo que pagó el cliente y los descuentos de la venta.",
        ])
        if turn:
            lines.append(f"Se registrará en el turno actual #{turn.pk}, cajero {bot._list_text(turn.cajero.nombreusuario, 80)}, de esta caja; no en un turno histórico cerrado.")
        elif total > 0:
            lines.append("El control de turnos está desactivado. El efectivo ajustará el saldo global del punto de pago de esta venta.")
        else:
            lines.append("Esta devolución no entrega dinero: solo restaura inventario y registra los productos devueltos.")
        lines.extend([
            "Las cantidades usan la unidad registrada de cada producto. El inventario regresa a la sucursal de la venta.",
            "Todavía no la he guardado. Pulsa Confirmar devolución solo si corresponde registrar la entrega del dinero por ese medio. El bot NO envía dinero por Nequi ni reversa cargos de tarjeta.",
            "La propuesta vence en 10 minutos. Si cambia la venta, la caja o el turno, deberás solicitar otra.",
        ])
        text = "\n".join(lines)
        if len(text) > 3600:
            raise bot.TelegramBotError("La propuesta no cabe completa en Telegram. Solicita menos productos por devolución para revisarla sin recortes.")
        pending = TelegramAccionPendiente.objects.create(
            telegram_usuario=profile, actualizacion=update, accion="devolver_venta",
            argumentos={
                "venta_id": sale.pk,
                "productos": [{"detalle_id": item["detalle"].pk, "cantidad": item["cantidad"]} for item in selected],
                "medio_pago": snapshot["medio"]["codigo"], "snapshot": snapshot,
            },
            resumen=f"Devolución venta #{sale.pk}: {bot._list_money(total)} en {snapshot['medio']['nombre']}"[:500],
            vence_en=timezone.now() + timedelta(minutes=bot.ACTION_TTL_MINUTES),
        )
    return bot.BotReply(text, "preparar_devolucion_venta", reply_markup={"inline_keyboard": [[
        {"text": "Confirmar devolución", "callback_data": f"confirm:{pending.pk}"},
        {"text": "Cancelar", "callback_data": f"cancel:{pending.pk}"},
    ]]})


def confirm_return(profile, action):
    """El callback mantiene bloqueada la propuesta durante esta transacción."""
    bot = _bot()
    user = _require_permission(profile)
    arguments = action.argumentos
    try:
        with transaction.atomic():
            sale, details = _load_sale(arguments.get("venta_id"))
            selected = _select_lines(details, arguments.get("productos"))
            total, turn, snapshot = _context(sale, details, selected, arguments.get("medio_pago"), confirming=True)
            if snapshot != arguments.get("snapshot"):
                raise bot.TelegramBotError("La venta, los productos, el medio o el turno cambiaron desde la propuesta. No se aplicó nada: solicita otra devolución para revisar los datos actuales.")
            original = snapshot["pagos_originales"]["reconstruir"]
            if original is not None:
                PagoVenta.objects.create(ventaid=sale, medio_pago=original["medio_pago"], monto=Decimal(original["monto"]))
            CambioDevolucion.registrar_devolucion(
                sale, selected,
                reintegro_map={snapshot["medio"]["codigo"]: total} if total > 0 else {},
                registrado_por=user, turno_requerido=snapshot["turno_requerido"],
            )
            action.estado = "CONFIRMADA"
            action.resuelto_en = timezone.now()
            action.save(update_fields=["estado", "resuelto_en"])
            bot._audit(profile, "confirmar_devolucion_venta", arguments, detail=f"Venta #{sale.pk}; reintegro {total}; turno {turn.pk if turn else 'sin turno'}")
        return f"Listo, registré la devolución de la venta #{sale.pk}: {bot._list_money(total)} en {snapshot['medio']['nombre']}. El inventario quedó actualizado y conservé el pago original. No se realizó ninguna transferencia bancaria automática."
    except (bot.TelegramBotError, ValueError, DatabaseError) as exc:
        # El savepoint ya revirtió inventario, venta, caja y reintegros. No hacer
        # consultas dentro de una transacción rota ni filtrar SQL al usuario.
        message = str(exc) if isinstance(exc, (bot.TelegramBotError, ValueError)) else "No fue posible guardar todos los cambios en la base de datos. No se aplicó la devolución; revisa el estado y solicita una nueva propuesta."
        action.estado = "ERROR"
        action.resuelto_en = timezone.now()
        action.save(update_fields=["estado", "resuelto_en"])
        bot._audit(profile, "confirmar_devolucion_venta", arguments, successful=False, detail=message)
        return "No se registró la devolución. " + message


def command_prepare_return(profile, text, update=None):
    bot = _bot()
    tokens = str(text or "").split()
    help_text = "Usa /devolver VENTA PRODUCTO:CANTIDAD [PRODUCTO:CANTIDAD ...] [MEDIO]. Ejemplo: /devolver 142266 2934:2 2941:1 efectivo. Si omites el medio, será efectivo."
    if not tokens:
        return bot.BotReply(help_text, "ayuda_devolucion")
    if not re.fullmatch(r"[1-9][0-9]{0,9}", tokens[0]):
        raise bot.TelegramBotError(help_text)
    products, index = [], 1
    while index < len(tokens):
        match = re.fullmatch(r"([1-9][0-9]{0,9}):([1-9][0-9]{0,9})", tokens[index])
        if not match:
            break
        products.append({"producto_id": int(match[1]), "cantidad": int(match[2])})
        index += 1
    if not products:
        raise bot.TelegramBotError(help_text)
    return bot._execute_tool(profile, "preparar_devolucion_venta", {
        "venta_id": int(tokens[0]), "productos": products,
        "medio_pago": " ".join(tokens[index:]) or CASH_PAYMENT_CODE,
    }, update)


RETURN_TOOL_DEFINITION = {
    "name": "preparar_devolucion_venta",
    "description": "Prepara devolver productos concretos de una venta y registrar su reintegro; requiere botón de confirmación. El monto lo calcula el sistema, no la IA. No transfiere dinero bancario. Efectivo por defecto.",
    "parameters": {"type": "OBJECT", "properties": {
        "venta_id": {"type": "INTEGER", "description": "ID real de la venta a devolver."},
        "productos": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "producto_id": {"type": "INTEGER", "description": "ID del producto, NO del detalle."},
            "detalle_id": {"type": "INTEGER", "description": "ID del renglón de venta, si el producto aparece varias veces."},
            "producto": {"type": "STRING", "description": "Nombre exacto en esta venta cuando no se conoce el ID. Usa SOLO uno de los tres identificadores."},
            "cantidad": {"type": "INTEGER", "description": "Cantidad explícita mayor que cero en la unidad registrada del producto; no asumir cantidades."},
        }, "required": ["cantidad"]}},
        "medio_pago": {"type": "STRING", "description": "Medio por el que se entrega el reintegro, independiente del pago original. Si no lo indica, efectivo. Un único medio activo por solicitud."},
    }, "required": ["venta_id", "productos"]},
}
