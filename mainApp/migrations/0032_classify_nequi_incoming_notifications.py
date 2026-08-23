from decimal import Decimal, InvalidOperation
import re
import unicodedata

from django.db import migrations


INCOMING_MARKERS = (
    "te envio",
    "te enviaron",
    "te llego plata",
    "te llego dinero",
    "te pagaron",
    "te consignaron",
    "te transfirieron",
    "te depositaron",
    "pago recibido",
    "plata recibida",
    "dinero recibido",
    "transferencia recibida",
    "recibiste plata",
    "recibiste dinero",
    "recibiste un pago",
    "recibiste una transferencia",
)

OUTGOING_MARKERS = (
    "enviaste",
    "transferiste",
    "pagaste",
    "hiciste un pago",
    "pago exitoso",
    "envio exitoso",
    "envio de plata exitoso",
    "tu plata llego con exito",
    "compra exitosa",
    "compra rechazada",
    "fondos insuficientes",
    "sacaste",
    "retiraste",
    "retiro en cajero",
    "nequi destino",
    "recarga pse",
    "de vuelta",
    "devolucion",
    "devuelto",
    "devuelta",
    "reversa",
    "reverso",
    "revertido",
    "revertida",
)


def _plain_text(value):
    return (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def _contains_marker(plain_text, marker):
    phrase = re.escape(marker).replace(r"\ ", r"\s+")
    return re.search(rf"(?<!\w){phrase}(?!\w)", plain_text) is not None


def _is_incoming_notification(title, text, amount):
    try:
        parsed_amount = Decimal(amount)
    except (InvalidOperation, TypeError, ValueError):
        return False
    if parsed_amount <= 0:
        return False

    plain_text = _plain_text(f"{title} {text}")
    if any(_contains_marker(plain_text, marker) for marker in OUTGOING_MARKERS):
        return False
    return any(
        _contains_marker(plain_text, marker)
        for marker in INCOMING_MARKERS
    )


def classify_existing_notifications(apps, schema_editor):
    NotificacionNequi = apps.get_model("mainApp", "NotificacionNequi")
    notifications = NotificacionNequi.objects.using(
        schema_editor.connection.alias
    )
    notifications.all().update(es_ingreso=False)

    incoming_ids = []
    rows = notifications.values_list(
        "notificacionid",
        "titulo",
        "texto",
        "monto",
    ).iterator(chunk_size=1000)
    for notification_id, title, text, amount in rows:
        if not _is_incoming_notification(title, text, amount):
            continue
        incoming_ids.append(notification_id)

    for start in range(0, len(incoming_ids), 1000):
        notifications.filter(
            pk__in=incoming_ids[start:start + 1000]
        ).update(es_ingreso=True)


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0031_notificacionnequi_es_ingreso"),
    ]

    operations = [
        migrations.RunPython(
            classify_existing_notifications,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
