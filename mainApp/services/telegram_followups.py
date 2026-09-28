"""Informes voluntarios, sin IA; reserva diaria para no duplicar envíos."""
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from .feature_flags import TELEGRAM_BOT_FEATURE, is_feature_enabled
from .telegram_business import require_business_access


def _report(rule, day):
    from mainApp.models import Inventario, TurnoCaja
    from . import telegram_bot as bot
    profile = rule.telegram_usuario
    require_business_access(profile, "seguimiento", {"tipo": rule.tipo})
    if rule.tipo == "resumen_diario":
        return bot.tool_balance(profile, {"desde": day.isoformat(), "hasta": day.isoformat(), "detalle": True})
    if rule.tipo == "diferencias_caja":
        rows = TurnoCaja.objects.filter(estado="CERRADO", fin__date=day).exclude(diferencia_total=0).select_related("cajero", "puntopago").order_by("pk")
        count = rows.count()
        if not count:
            return None
        lines = [f"{count} cierres con diferencia hoy:"]
        lines.extend(f"• #{r.pk} · {r.puntopago.nombre} · {r.cajero.nombreusuario}: {bot._list_money(r.diferencia_total)}" for r in rows[:10])
        if count > 10:
            lines.append("Mostré 10. Consulta el dashboard para revisar todos.")
        lines.append("Una diferencia requiere revisión; por sí sola no demuestra una irregularidad.")
        return "\n".join(lines)
    rows = Inventario.objects.filter(cantidad__lte=0).select_related("productoid", "sucursalid").order_by("cantidad", "pk")
    count = rows.count()
    if not count:
        return None
    lines = [f"Hay {count} registros de inventario con cero o menos:"]
    lines.extend(f"• #{r.productoid_id} {r.productoid.nombre} · {r.sucursalid.nombre}: {r.cantidad}" for r in rows[:10])
    if count > 10:
        lines.append("Mostré 10. Pídeme el inventario de una sucursal para afinar.")
    lines.append("Son existencias registradas; conviene contrastarlas con el conteo físico.")
    return "\n".join(lines)


def process_followups(*, now=None, client=None, limit=5):
    from mainApp.models import TelegramEnvioSeguimiento, TelegramSeguimiento
    from . import telegram_bot as bot
    if not is_feature_enabled(TELEGRAM_BOT_FEATURE, fresh=True):
        return 0
    local = timezone.localtime(now or timezone.now(), ZoneInfo("America/Bogota"))
    day = local.date()
    pending = TelegramSeguimiento.objects.filter(
        activo=True, hora__lte=local.time().replace(tzinfo=None),
        telegram_usuario__activo=True, telegram_usuario__usuario__is_active=True,
    ).exclude(envios__fecha=day).order_by("pk").values_list("pk", flat=True)[:max(1, min(limit, 20))]
    processed = 0
    for pk in list(pending):
        try:
            with transaction.atomic():
                rule = TelegramSeguimiento.objects.select_for_update().select_related("telegram_usuario__usuario").get(pk=pk)
                if not rule.activo or rule.hora > local.time().replace(tzinfo=None):
                    continue
                delivery, created = TelegramEnvioSeguimiento.objects.get_or_create(seguimiento=rule, fecha=day)
                if not created:
                    continue
        except (IntegrityError, TelegramSeguimiento.DoesNotExist):
            continue
        # La reserva se confirma ANTES de la petición externa. Si el proceso
        # muere o Telegram recibe pero la conexión falla, no enviar dos veces.
        processed += 1
        try:
            rule.refresh_from_db()
            profile = rule.telegram_usuario
            if (not rule.activo or rule.hora > local.time().replace(tzinfo=None)
                    or not is_feature_enabled(TELEGRAM_BOT_FEATURE, fresh=True)
                    or profile.telegram_chat_id != profile.telegram_user_id):
                raise PermissionDenied("Seguimiento pausado o chat no privado.")
            with timezone.override("America/Bogota"):
                text = _report(rule, day)
            if text is None:
                delivery.estado, delivery.detalle = "OMITIDO", "Sin novedades para este informe."
            else:
                (client or bot.TelegramApiClient()).send_message(profile.telegram_chat_id, text)
                delivery.estado, delivery.detalle = "ENVIADO", "Informe enviado."
        except PermissionDenied:
            delivery.estado, delivery.detalle = "OMITIDO", "Cuenta, permiso, destino o seguimiento ya no habilitado."
        except Exception:
            # No guardar excepciones de transporte: podrían incluir el token.
            delivery.estado, delivery.detalle = "ERROR_INCIERTO", "No se pudo confirmar el envío; no se reintenta automáticamente."
        delivery.save(update_fields=["estado", "detalle"])
    return processed
