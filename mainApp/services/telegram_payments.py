"""Consultas y correcciones de pagos; el chat reutiliza las reglas de la web."""
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from mainApp.forms import EditarEgresoForm
from mainApp.models import Egreso, TelegramAccionPendiente
from .expense_editing import edit_operational_expense, expense_edit_token, expense_editing_ready
from .operational_expenses import OperationalExpenseError, find_similar_expense_concepts
from .payment_methods import payment_method_label, payment_method_options


def _bot():
    from . import telegram_bot
    return telegram_bot


def _expense(profile, payment_id, *, edit=False):
    bot = _bot()
    bot._require_access(profile, "editar_egreso" if edit else "registrar_egreso")
    if edit and not expense_editing_ready():
        raise bot.TelegramBotError("La edición de pagos aún no está habilitada. El administrador debe aplicar la migración 0041; no cambié ningún pago.")
    row = Egreso.objects.select_related("concepto").filter(pk=payment_id).first()
    if row is None:
        raise bot.TelegramBotError("No encontré ese pago. Comprueba su número o pídeme la lista de pagos.")
    return row


def tool_payment(profile, arguments):
    bot = _bot()
    history = arguments.get("historial", False)
    expense = _expense(profile, arguments["pago_id"], edit=history)
    date = timezone.localtime(expense.creado_en).strftime("%d/%m/%Y %H:%M")
    lines = [f"Pago #{expense.pk}: {expense.concepto.nombre}",
             f"{bot._list_money(expense.monto)} en {payment_method_label(expense.medio_pago)}.",
             f"Fecha del pago: {date}. Lo registró {bot._list_text(expense.registrado_por_nombre)}."]
    pagination = None
    if history:
        rows = expense.cambios.all()
        count = rows.count()
        page, pages, offset = bot._list_page(arguments, count)
        if not count:
            lines.append("Este pago no tiene correcciones registradas.")
        for change in rows[offset:offset + bot.LIST_PAGE_SIZE]:
            before, after = change.anterior, change.nuevo
            lines.append(f"\n{bot._list_text(change.usuario_nombre)} · {timezone.localtime(change.creado_en):%d/%m/%Y %H:%M}")
            for key, label in (("concepto", "Concepto"), ("monto", "Valor"), ("medio_pago", "Medio"), ("fecha_pago_texto", "Fecha del pago")):
                if before.get(key) == after.get(key):
                    continue
                fmt = bot._list_money if key == "monto" else payment_method_label if key == "medio_pago" else bot._list_text
                lines.append(f"• {label}: {fmt(before.get(key))} → {fmt(after.get(key))}")
            lines.append(f"Motivo: {bot._list_text(change.motivo, 300)}")
        pagination = {"page": page, "pages": pages, "arguments": dict(arguments, pagina=page)}
    return bot.BotReply("\n".join(lines), "consultar_pago", pagination=pagination)


def tool_prepare_payment_edit(profile, arguments, update=None):
    bot = _bot()
    expense = _expense(profile, arguments["pago_id"], edit=True)
    changed_keys = set(arguments) & {"concepto", "monto", "medio_pago"}
    if not changed_keys:
        raise bot.TelegramClarification("¿Qué quieres corregir de ese pago: el concepto, el valor o el medio de pago?")
    if not str(arguments.get("motivo") or "").strip():
        raise bot.TelegramClarification("¿Cuál es el motivo de la corrección? Lo dejaré en el historial del pago.")
    methods = payment_method_options(active_only=True)
    data = {
        "concepto": arguments.get("concepto", expense.concepto.nombre),
        "monto": Decimal(str(arguments["monto"])) if "monto" in arguments else expense.monto,
        "medio_pago": bot._resolve_payment_method(arguments["medio_pago"]) if "medio_pago" in arguments else expense.medio_pago,
        "motivo": arguments["motivo"], "version": expense_edit_token(expense, profile.usuario),
    }
    form = EditarEgresoForm(data, payment_methods=methods,
                           current_method=(expense.medio_pago, payment_method_label(expense.medio_pago)))
    if not form.is_valid():
        raise bot.TelegramClarification("Revisa estos datos: " + "; ".join(
            f"{form.fields[name].label or 'Pago'}: {' '.join(errors)}" for name, errors in form.errors.items()
        ))
    clean = form.cleaned_data
    if "concepto" in changed_keys and not arguments.get("concepto_nuevo"):
        from mainApp.models import ConceptoEgreso
        if not ConceptoEgreso.objects.filter(nombre=clean["concepto"]).exists():
            similar = find_similar_expense_concepts(clean["concepto"])
            if similar:
                options = "; ".join(item["nombre"] for item in similar)
                raise bot.TelegramClarification(f"Ya hay conceptos parecidos: {options}. ¿Cuál quieres usar, o prefieres crear «{clean['concepto']}» como concepto nuevo?")
    old = {"concepto": expense.concepto.nombre, "monto": expense.monto, "medio_pago": expense.medio_pago}
    labels = {"concepto": "Concepto", "monto": "Valor", "medio_pago": "Medio de pago"}
    changes = [key for key in old if old[key] != clean[key]]
    if not changes:
        return bot.BotReply("Ese pago ya está guardado así. No hay nada que corregir.", "sin_cambios")
    lines = [f"¿Confirmas esta corrección del pago #{expense.pk}?"]
    for key in changes:
        fmt = bot._list_money if key == "monto" else payment_method_label if key == "medio_pago" else bot._list_text
        lines.append(f"• {labels[key]}: {fmt(old[key])} → {fmt(clean[key])}")
    lines += [f"Motivo: {bot._list_text(clean['motivo'], 300)}",
              "La fecha y quien registró el pago se conservan. Todavía no guardé el cambio; confirma antes de 10 minutos."]
    pending = TelegramAccionPendiente.objects.create(
        telegram_usuario=profile, actualizacion=update, accion="editar_pago",
        argumentos={"pago_id": expense.pk, **{key: str(clean[key]) for key in ("concepto", "monto", "medio_pago", "motivo", "version")}},
        resumen=f"Corregir pago #{expense.pk}: {clean['concepto']}"[:500],
        vence_en=timezone.now() + timedelta(minutes=bot.ACTION_TTL_MINUTES),
    )
    return bot.BotReply("\n".join(lines), "preparar_edicion_pago", reply_markup={"inline_keyboard": [[
        {"text": "Confirmar corrección", "callback_data": f"confirm:{pending.pk}"},
        {"text": "Cancelar", "callback_data": f"cancel:{pending.pk}"},
    ]]})


def confirm_payment_edit(profile, action):
    bot = _bot()
    bot._require_access(profile, "editar_egreso")
    args = action.argumentos
    try:
        with transaction.atomic():
            expense, _changed = edit_operational_expense(
                user=profile.usuario, expense_id=args["pago_id"], concept=args["concepto"],
                amount=args["monto"], payment_method=args["medio_pago"],
                reason=args["motivo"], version=args["version"],
            )
            action.estado = "CONFIRMADA"
            action.resuelto_en = timezone.now()
            action.save(update_fields=["estado", "resuelto_en"])
            bot._audit(profile, "confirmar_edicion_pago", {"pago_id": expense.pk})
        return f"Listo, corregí el pago #{expense.pk}: {bot._list_text(expense.concepto.nombre)}, {bot._list_money(expense.monto)} en {payment_method_label(expense.medio_pago)}. El cambio quedó en su historial."
    except (OperationalExpenseError, Egreso.DoesNotExist, IntegrityError) as exc:
        message = str(exc) if isinstance(exc, OperationalExpenseError) else "El pago cambió o sus datos entran en conflicto. Pídeme una nueva propuesta."
        action.estado = "ERROR"
        action.resuelto_en = timezone.now()
        action.save(update_fields=["estado", "resuelto_en"])
        bot._audit(profile, "confirmar_edicion_pago", {"pago_id": args.get("pago_id")}, successful=False, detail=message)
        return "No guardé la corrección. " + message


TOOL_DEFINITIONS = [
    {"name": "consultar_pago", "description": "Consulta un pago por su ID, sin exigir fecha. historial=true solo si pide quién lo corrigió, los cambios o sus motivos; requiere permiso de edición.", "parameters": {"type": "OBJECT", "properties": {
        "pago_id": {"type": "INTEGER"}, "historial": {"type": "BOOLEAN"}, "pagina": {"type": "INTEGER"},
    }, "required": ["pago_id"]}},
    {"name": "preparar_edicion_pago", "description": "Propone corregir un pago existente. Conserva fecha y creador; registra auditoría. Envía solo los campos solicitados y pregunta el motivo si falta. Nunca confunde corregir con registrar otro pago. No guarda hasta pulsar Confirmar corrección.", "parameters": {"type": "OBJECT", "properties": {
        "pago_id": {"type": "INTEGER"}, "concepto": {"type": "STRING"}, "monto": {"type": "NUMBER"},
        "medio_pago": {"type": "STRING"}, "motivo": {"type": "STRING", "description": "Motivo dicho por el usuario; no inventarlo."},
        "concepto_nuevo": {"type": "BOOLEAN", "description": "Solo true si el usuario confirmó que quiere un concepto nuevo frente a los parecidos."},
    }, "required": ["pago_id"]}},
]
TOOL_FUNCTIONS = {"consultar_pago": tool_payment, "preparar_edicion_pago": tool_prepare_payment_edit}
