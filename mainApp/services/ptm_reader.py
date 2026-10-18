"""Observaciones del portal PTM. Ninguna función mueve efectivo ni ejecuta PTM."""
import hashlib
import re
import secrets
import unicodedata
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from mainApp.models import AuditoriaLecturaPTM, LectorPTM, MovimientoLeidoPTM, OperacionPTM

BOGOTA = ZoneInfo("America/Bogota")
FIELDS = ("id_operador", "inicio", "fin", "producto", "valor", "estado_ptm", "tipo")
READER_STATES = {"ok", "sesion", "estructura", "conexion", "parcial", "limite", "cuenta"}


def reference(value):
    return "".join(unicodedata.normalize("NFKC", str(value or "")).upper().split())


def issue_reader(*, account, name, actor):
    secret = secrets.token_urlsafe(32)
    reader = LectorPTM.objects.create(cuenta=account, nombre=name, creado_por=actor,
        token_hash=hashlib.sha256(secret.encode()).hexdigest(), vence_en=timezone.now() + timedelta(days=90))
    return reader, f"{reader.pk}.{secret}"


def authenticate_reader(token):
    from mainApp.permissions import user_can_access_url_name
    try:
        pk, secret = token.split(".", 1)
        reader = LectorPTM.objects.select_related("cuenta", "creado_por").filter(pk=pk, activo=True).first()
    except (ValueError, ValidationError, AttributeError):
        return None
    if not reader or reader.vence_en <= timezone.now():
        return None
    digest = hashlib.sha256(secret.encode()).hexdigest()
    if not secrets.compare_digest(reader.token_hash, digest):
        return None
    if not reader.creado_por.is_active or not user_can_access_url_name(reader.creado_por, "conciliacion_ptm"):
        return None
    return reader


def _text(row, key, length, required=True):
    value = row.get(key, "")
    if not isinstance(value, str) or len(value) > length or any(ord(c) < 32 for c in value):
        raise ValidationError(f"Campo PTM inválido: {key}.")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"Falta el campo PTM: {key}.")
    return value


def _date(value):
    try:
        return datetime.strptime(value, "%Y/%m/%d %H:%M:%S").replace(tzinfo=BOGOTA)
    except ValueError:
        raise ValidationError("Fecha PTM inválida.")


def parse_row(row, account, report_date):
    if not isinstance(row, dict):
        raise ValidationError("Fila PTM inválida.")
    if _text(row, "usuario", 100) != account.usuario_ptm:
        raise ValidationError("El usuario del reporte no corresponde a la cuenta PTM configurada.")
    tx = reference(_text(row, "id_tx", 100))
    operator = reference(_text(row, "id_operador", 100, False))
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{0,99}", tx) or (operator and not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{0,99}", operator)):
        raise ValidationError("La referencia PTM no es válida.")
    start = _date(_text(row, "inicio", 19))
    end = _text(row, "fin", 19, False)
    if start.date() != report_date or start > timezone.now() + timedelta(minutes=5):
        raise ValidationError("La fecha de la fila no coincide con la consulta.")
    amount = _text(row, "valor", 24)
    # El lector envía decimal canónico, nunca un float ni separadores de miles.
    if not re.fullmatch(r"-?\d{1,12}\.\d{2}", amount):
        raise ValidationError("Importe PTM inválido.")
    try:
        amount = Decimal(amount)
    except InvalidOperation:
        raise ValidationError("Importe PTM inválido.")
    product = _text(row, "producto", 150)
    normalized = reference(product)
    # Solo tipos comprobados en el reporte real. No inferir otros productos por signo.
    kind = "retiro" if normalized == "NEQUI-RETIROS" and amount < 0 else (
        "recarga" if normalized == "NEQUI-RECARGAS" and amount > 0 else "")
    return dict(id_tx=tx, id_operador=operator, inicio=start, fin=_date(end) if end else None,
                producto=product, valor=amount, estado_ptm=_text(row, "estado", 80), tipo=kind)


def snapshot(movement):
    def canonical(value):
        if isinstance(value, datetime):
            return value.astimezone(BOGOTA).isoformat()
        return str(value if value is not None else "")
    return {key: canonical(getattr(movement, key)) for key in FIELDS}


def reconciliation(movement):
    if reference(movement.estado_ptm) != "EXITOSA" or not movement.tipo:
        return "diferencia" if movement.operacion_id else "revisar"
    if not movement.operacion_id:
        return "sin_vincular"
    op = movement.operacion
    matches = (reference(op.referencia) in {movement.id_tx, movement.id_operador}
               and op.tipo == movement.tipo and op.monto == abs(movement.valor))
    return "vinculado" if matches else "diferencia"


@transaction.atomic
def ingest(reader, payload):
    if not isinstance(payload, dict):
        raise ValidationError("El reporte debe ser un objeto JSON.")
    try:
        day = datetime.strptime(payload.get("fecha", ""), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise ValidationError("Fecha del reporte inválida.")
    if day > timezone.now().astimezone(BOGOTA).date():
        raise ValidationError("No se aceptan reportes futuros.")
    observed_text = payload.get("leido_en", "")
    observed = parse_datetime(observed_text) if isinstance(observed_text, str) and len(observed_text) <= 40 else None
    if (not observed or timezone.is_naive(observed) or observed > timezone.now() + timedelta(minutes=5)
            or observed.astimezone(BOGOTA).date() < day):
        raise ValidationError("La hora de lectura no es válida. Revisa el reloj del computador lector.")
    rows = payload.get("filas")
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValidationError("Cada lote admite hasta 100 filas.")
    parsed = [parse_row(row, reader.cuenta, day) for row in rows]
    if len({r["id_tx"] for r in parsed}) != len(parsed):
        raise ValidationError("El reporte repite un ID TX en el mismo lote.")
    # Serializa lectores de una misma cuenta, también en la primera importación.
    type(reader.cuenta).objects.select_for_update().get(pk=reader.cuenta_id)
    created = changed = stale = 0
    changed_times = []
    now = timezone.now()
    for row in parsed:
        movement, new = MovimientoLeidoPTM.objects.get_or_create(cuenta=reader.cuenta, id_tx=row["id_tx"],
            defaults={**row, "ultima_lectura": now, "observado_en": observed})
        # Coordinate imports with manual linking; never overwrite a concurrent link.
        movement = MovimientoLeidoPTM.objects.select_for_update().get(pk=movement.pk)
        if not new and observed < movement.observado_en:
            stale += 1
            continue
        before = snapshot(movement)
        old_start = movement.inicio
        for key in FIELDS:
            setattr(movement, key, row[key])
        movement.ultima_lectura = now
        movement.observado_en = observed
        movement.conciliacion = reconciliation(movement)
        movement.save()
        after = snapshot(movement)
        if new or before != after:
            changed_times.extend([old_start, movement.inicio])
            AuditoriaLecturaPTM.objects.create(movimiento=movement, lector=reader,
                accion="importado" if new else "actualizado", detalle={"antes": {} if new else before, "despues": after})
        created += int(new)
        changed += int(not new and before != after)
    reader.ultimo_contacto = now
    reader.estado = "parcial"
    reader.save(update_fields=["ultimo_contacto", "estado"])
    if changed_times:
        from .ptm_cash_pool import refresh_cuts
        refresh_cuts(reader.cuenta_id, changed_times)
    return {"recibidas": len(rows), "nuevas": created, "actualizadas": changed, "antiguas_ignoradas": stale}


@transaction.atomic
def link(*, movement_id, operation_id, actor, reason, unlink=False):
    reason = str(reason or "").strip()
    if len(reason) < 5 or len(reason) > 300:
        raise ValidationError("Escribe un motivo de 5 a 300 caracteres para dejar trazabilidad.")
    movement = MovimientoLeidoPTM.objects.select_for_update().get(pk=movement_id)
    old_id = movement.operacion_id
    if unlink:
        movement.operacion = None
    else:
        op = OperacionPTM.objects.select_for_update().get(pk=operation_id)
        if old_id and old_id != op.pk:
            raise ValidationError("Desvincula primero la asociación anterior y deja el motivo.")
        if reference(op.referencia) not in {movement.id_tx, movement.id_operador}:
            raise ValidationError("La referencia del POS no coincide con el ID TX ni con el ID Operador.")
        if MovimientoLeidoPTM.objects.filter(operacion=op).exclude(pk=movement.pk).exists():
            raise ValidationError("Esta operación del POS ya está vinculada a otro movimiento.")
        movement.operacion = op
    movement.conciliacion = reconciliation(movement)
    movement.save(update_fields=["operacion", "conciliacion"])
    AuditoriaLecturaPTM.objects.create(movimiento=movement, usuario=actor,
        accion="desvinculado" if unlink else "vinculado",
        detalle={"anterior": old_id, "operacion": movement.operacion_id, "motivo": reason})
    return movement
