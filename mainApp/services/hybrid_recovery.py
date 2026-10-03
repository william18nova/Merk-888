"""Recuperación en dos fases: retiro, revisión humana y conciliación atómica."""
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from mainApp.models import EquipoHibrido, SesionHibrida, OperacionHibrida, RecuperacionHibrida
from mainApp.permissions import is_web_master_role
from pos_shared.protocol import fingerprint, canonical
from . import hybrid

OPEN_STATES = ("issued", "review", "approved")


def require_admin(actor):
    if not actor.is_active or not is_web_master_role(actor):
        raise hybrid.HybridError("Solo un Web Master activo puede autorizar recuperaciones.", "permission", 403)


@transaction.atomic
def authorize(*, actor, device_id, reason, isolated=False):
    require_admin(actor)
    hybrid.require_enabled()
    reason = " ".join(str(reason).split())
    if not isolated or not 10 <= len(reason) <= 250:
        raise hybrid.HybridError("Confirma que retiraste el equipo anterior y escribe el motivo (10–250 caracteres).")
    device = EquipoHibrido.objects.select_for_update().get(pk=hybrid.identifier(device_id), activo=True)
    # Reemitir invalida códigos anteriores, pero conserva sus registros de auditoría.
    RecuperacionHibrida.objects.filter(equipo=device, estado__in=OPEN_STATES).update(estado="superseded")
    code = secrets.token_urlsafe(32)
    record = RecuperacionHibrida.objects.create(equipo=device, creada_por=actor, motivo=reason,
        codigo_hash=hybrid.digest(code), vence_en=timezone.now() + timedelta(minutes=15))
    device.token_hash = ""
    device.enlace_hash = ""
    device.enlace_vence = timezone.now()
    device.save(update_fields=["token_hash", "enlace_hash", "enlace_vence"])
    return record, f"{record.pk}.{code}"


def lock_record(pk):
    stub = RecuperacionHibrida.objects.get(pk=hybrid.identifier(pk))
    device = EquipoHibrido.objects.select_for_update(of=("self",)).select_related("punto__sucursalid").get(pk=stub.equipo_id)
    record = RecuperacionHibrida.objects.select_for_update(of=("self",)).select_related("creada_por", "aprobada_por").get(pk=stub.pk)
    return device, record


def manifest(data):
    bundle = data.get("manifest")
    if not isinstance(bundle, dict) or set(bundle) != {"device_id", "backup_created_at", "operations"}:
        raise hybrid.HybridError("Manifiesto de recuperación inválido.", "manifest", 400)
    created = parse_datetime(str(bundle["backup_created_at"]))
    if created is None or timezone.is_naive(created) or created > timezone.now() + timedelta(minutes=2):
        raise hybrid.HybridError("Fecha del respaldo inválida.", "manifest", 400)
    rows = bundle["operations"]
    if not isinstance(rows, list) or len(rows) > 1000 or len(canonical(bundle).encode()) > 1800000:
        raise hybrid.HybridError("El respaldo supera el límite de recuperación del piloto (1000 operaciones / 1,8 MB de manifiesto). Requiere revisión asistida; no recortes el historial.", "recovery_size", 400)
    identities, sequences = set(), set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"state", "payload"} or row["state"] not in {"accepted", "pending", "conflict"} or not isinstance(row["payload"], dict):
            raise hybrid.HybridError("Operación del respaldo inválida.", "manifest", 400)
        payload = row["payload"]
        op_id, session_id = hybrid.identifier(payload.get("operation_id")), hybrid.identifier(payload.get("session_id"))
        seq = payload.get("sequence")
        if type(seq) is not int or seq < 1 or op_id in identities or (session_id, seq) in sequences:
            raise hybrid.HybridError("Identificadores o secuencias repetidos en el respaldo.", "manifest", 400)
        occurred = parse_datetime(str(payload.get("occurred_at", "")))
        if occurred is None or timezone.is_naive(occurred) or occurred > created:
            raise hybrid.HybridError("Hay una venta posterior a la fecha del respaldo.", "manifest", 400)
        identities.add(op_id)
        sequences.add((session_id, seq))
    return bundle


def plan(device, bundle):
    if str(device.pk) != bundle["device_id"]:
        raise hybrid.HybridError("El respaldo pertenece a otro equipo. No se puede recuperar en esta caja.", "device", 409)
    sessions = {str(s.pk): s for s in SesionHibrida.objects.filter(equipo=device)}
    ledger = {str(row.pk): row for row in OperacionHibrida.objects.filter(sesion__equipo=device)}
    positions = {(str(row.sesion_id), row.secuencia): row for row in ledger.values()}
    missing, found = [], []
    for row in bundle["operations"]:
        payload = row["payload"]
        op_id, sid, seq = payload["operation_id"], payload["session_id"], payload["sequence"]
        session = sessions.get(sid)
        if session is None:
            raise hybrid.HybridError("Una sesión del respaldo no pertenece al equipo autorizado.", "session", 409)
        old = ledger.get(op_id)
        if old:
            if old.huella != fingerprint(payload) or str(old.sesion_id) != sid or old.secuencia != seq:
                raise hybrid.HybridError("Una venta del respaldo no coincide con lo recibido en la nube. Revisión manual necesaria.", "idempotency", 409)
            found.append(old)
        else:
            if row["state"] == "accepted" or session.liberada_en or seq <= session.secuencia or (sid, seq) in positions or OperacionHibrida.objects.filter(pk=op_id).exists():
                raise hybrid.HybridError("Una confirmación o secuencia no coincide con la nube. No se recreó ninguna venta.", "sequence", 409)
            missing.append(payload)
    expected = {sid: s.secuencia for sid, s in sessions.items()}
    missing.sort(key=lambda p: (p["session_id"], p["sequence"]))
    for payload in missing:
        sid = payload["session_id"]
        if payload["sequence"] != expected[sid] + 1:
            raise hybrid.HybridError("Faltan ventas intermedias en el respaldo. No se saltará ninguna secuencia.", "sequence", 409)
        expected[sid] += 1
    info = {"backup_created_at": bundle["backup_created_at"], "total": len(bundle["operations"]),
            "already_received": len(found), "to_upload": len(missing), "cloud_only": len(ledger) - len(found),
            "sessions_to_release": [sid for sid, s in sessions.items() if not s.liberada_en],
            "operations": [{"id": p["operation_id"], "session": p["session_id"], "sequence": p["sequence"], "hash": fingerprint(p)} for p in missing]}
    return info, missing


def _secret(raw):
    if not isinstance(raw, str) or len(raw) != 64 or any(c not in "0123456789abcdef" for c in raw):
        raise hybrid.HybridError("Credencial de reemplazo inválida.", "authentication", 401)
    return hybrid.digest(raw)


@transaction.atomic
def prepare(data):
    hybrid.require_enabled()
    try:
        pk, code = str(data.get("code", "")).split(".", 1)
        device, record = lock_record(pk)
    except (ValueError, RecuperacionHibrida.DoesNotExist):
        raise hybrid.HybridError("Código de recuperación inválido.", "authentication", 401) from None
    if not device.activo or record.estado not in OPEN_STATES or record.vence_en < timezone.now() or not secrets.compare_digest(record.codigo_hash, hybrid.digest(code)):
        raise hybrid.HybridError("Código vencido o reemplazado. Solicita nueva autorización al Web Master.", "authentication", 401)
    hashed = _secret(data.get("secret"))
    bundle = manifest(data)
    signature = fingerprint(bundle)
    if record.manifiesto_hash:
        if record.manifiesto_hash != signature or record.nuevo_token_hash != hashed:
            raise hybrid.HybridError("Esta autorización ya está ligada a otro respaldo/equipo de reemplazo.", "idempotency", 409)
    else:
        info, _ = plan(device, bundle)
        record.manifiesto_hash, record.nuevo_token_hash = signature, hashed
        record.resumen, record.estado = info, "review"
        record.vence_en = timezone.now() + timedelta(hours=24)
        record.save(update_fields=["manifiesto_hash", "nuevo_token_hash", "resumen", "estado", "vence_en"])
    return {"recovery_id": str(record.pk), "state": record.estado, "summary": record.resumen}


@transaction.atomic
def approve(*, actor, recovery_id, understood=False):
    require_admin(actor)
    device, record = lock_record(recovery_id)
    if not understood or record.estado != "review" or record.vence_en < timezone.now() or not device.activo:
        raise hybrid.HybridError("Revisa el resumen y confirma sus límites antes de aprobar; la solicitud debe estar vigente.")
    record.estado, record.aprobada_por, record.aprobada_en = "approved", actor, timezone.now()
    record.save(update_fields=["estado", "aprobada_por", "aprobada_en"])
    return record


@transaction.atomic
def finish(data):
    hybrid.require_enabled()
    try:
        device, record = lock_record(data.get("recovery_id"))
    except RecuperacionHibrida.DoesNotExist:
        raise hybrid.HybridError("Recuperación no encontrada.", "authentication", 401) from None
    hashed = _secret(data.get("secret"))
    if not record.nuevo_token_hash or not secrets.compare_digest(record.nuevo_token_hash, hashed):
        raise hybrid.HybridError("Credencial de recuperación incorrecta.", "authentication", 401)
    bundle = manifest(data)
    if record.manifiesto_hash != fingerprint(bundle):
        raise hybrid.HybridError("El respaldo cambió después de su revisión. No se aplicó nada.", "idempotency", 409)
    if record.estado == "completed":
        if not device.activo or device.token_hash != hashed:
            raise hybrid.HybridError("Esta instalación ya fue reemplazada por otra recuperación.", "authentication", 401)
        return record.resultado
    if record.estado not in ("review", "approved") or record.vence_en < timezone.now() or not device.activo:
        raise hybrid.HybridError("Autorización vencida o reemplazada. Conserva el respaldo y solicita revisión.", "recovery", 409)
    if record.estado == "review":
        return {"state": "review", "recovery_id": str(record.pk), "summary": record.resumen}
    require_admin(record.aprobada_por)
    info, missing = plan(device, bundle)
    if info != record.resumen:
        raise hybrid.HybridError("La situación de la nube cambió tras la revisión. Solicita una nueva autorización.", "changed", 409)
    # Todo el lote y su auditoría se confirman juntos. Si una fila falla, ninguna
    # venta nueva queda parcialmente aplicada ni se habilita la nueva credencial.
    for payload in missing:
        hybrid.accept_sale(device, payload, recovery=record)
    acknowledgements = {str(row.pk): row.respuesta for row in OperacionHibrida.objects.filter(pk__in=[r["payload"]["operation_id"] for r in bundle["operations"]], sesion__equipo=device)}
    if len(acknowledgements) != len(bundle["operations"]):
        raise hybrid.HybridError("La conciliación no confirmó todas las operaciones.", "pending", 409)
    now = timezone.now()
    SesionHibrida.objects.filter(equipo=device, liberada_en__isnull=True).update(liberada_en=now)
    device.token_hash, device.visto_en = hashed, now
    device.save(update_fields=["token_hash", "visto_en"])
    record.estado, record.completada_en = "completed", now
    record.resultado = {"state": "completed", "recovery_id": str(record.pk), "device_id": str(device.pk),
                       "name": device.nombre, "point": device.punto.nombre,
                       "acknowledgements": acknowledgements, "summary": info}
    record.save(update_fields=["estado", "completada_en", "resultado"])
    return record.resultado
