"""Cortes compartidos PTM; incidencias, nunca descuentos de nómina ni dinero de caja.

El reporte PTM es la única fuente de operaciones de la bolsa. Los registros
manuales del POS sirven para conciliación y no se suman por segunda vez.
Cada período parte del contado anterior: un faltante histórico no se hereda.
"""
import hashlib
import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from mainApp.models import (AjusteBolsaPTM, AuditoriaBolsaPTM, BolsaEfectivoPTM,
    CorteBolsaPTM, CuentaLecturaPTM, LecturaDiariaPTM, MovimientoLeidoPTM,
    OperacionPTM, ParticipacionBolsaPTM, TurnoCaja)
from .ptm_reader import BOGOTA, reference

ZERO = Decimal("0.00")
SALT = "novapos.ptm.cash-pool.count.v1"


def online_only():
    from local_pos.request_context import offline_request
    if offline_request():
        raise ValidationError("Estamos offline. La bolsa PTM requiere conexión y una lectura del portal.")


def is_admin(actor):
    from mainApp.permissions import is_web_master_role
    return actor.is_authenticated and is_web_master_role(actor)


def admin_required(actor):
    online_only()
    if not is_admin(actor):
        raise ValidationError("Solo Web Master puede configurar o revisar la bolsa PTM.")


def money(value, signed=False):
    try:
        amount = Decimal(str(value))
        if (not amount.is_finite() or abs(amount) > Decimal("999999999999.99")
                or amount != amount.quantize(Decimal("0.01")) or (not signed and amount < 0)):
            raise ValueError
        return amount.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError("Escribe un importe válido, no negativo y con máximo dos decimales.")


def bag_for_turn(turn):
    return BolsaEfectivoPTM.objects.filter(sucursal_id=turn.puntopago.sucursalid_id).first()


def lock_bag(bag):
    # Orden común con el importador: cuenta -> bolsa. No bloquea otros turnos.
    CuentaLecturaPTM.objects.select_for_update().get(pk=bag.cuenta_id)
    return BolsaEfectivoPTM.objects.select_for_update().get(pk=bag.pk)


@transaction.atomic
def configure(*, actor, branch, account, base):
    admin_required(actor)
    CuentaLecturaPTM.objects.select_for_update().get(pk=account.pk)
    if BolsaEfectivoPTM.objects.filter(sucursal=branch).exists() or BolsaEfectivoPTM.objects.filter(cuenta=account).exists():
        raise ValidationError("La sucursal o la cuenta ya tiene una bolsa. Registra un ajuste; no sobrescribas su base.")
    if TurnoCaja.objects.filter(puntopago__sucursalid=branch, estado="CIERRE").exists():
        raise ValidationError("Termina los cierres pendientes de esta sucursal antes de activar la bolsa.")
    now = timezone.now()
    bag = BolsaEfectivoPTM.objects.create(sucursal=branch, cuenta=account,
        base_inicial=money(base), inicio=now, creada_por=actor)
    # No se incorporan turnos sin haber contado/recibido la bolsa.
    AuditoriaBolsaPTM.objects.create(bolsa=bag, actor=actor, accion="apertura",
        detalle={"base": str(bag.base_inicial), "desde": now.isoformat()})
    return bag


@transaction.atomic
def adjust(*, actor, bag, amount, reason, request_id):
    admin_required(actor)
    bag = lock_bag(bag)
    amount = money(amount, signed=True)
    reason = str(reason or "").strip()
    if not amount or not 5 <= len(reason) <= 300:
        raise ValidationError("Indica una entrada/salida distinta de cero y un motivo de 5 a 300 caracteres.")
    previous = AjusteBolsaPTM.objects.filter(solicitud=request_id).first()
    if previous:
        if (previous.bolsa_id, previous.monto, previous.motivo, previous.actor_id) != (bag.pk, amount, reason, actor.pk):
            raise ValidationError("La solicitud ya fue utilizada con otros datos.")
        return previous
    row = AjusteBolsaPTM.objects.create(bolsa=bag, monto=amount, motivo=reason,
        actor=actor, creado_en=timezone.now(), solicitud=request_id)
    bag.version += 1
    bag.save(update_fields=["version"])
    AuditoriaBolsaPTM.objects.create(bolsa=bag, actor=actor, accion="ajuste",
        detalle={"ajuste": row.pk, "monto": str(amount), "motivo": reason})
    return row


def sources(bag, start, end):
    total = ZERO
    warnings = []
    fingerprint = []
    rows = MovimientoLeidoPTM.objects.filter(cuenta_id=bag.cuenta_id, inicio__gte=start, inicio__lt=end).order_by("pk")
    references = {}
    for row in rows:
        state = reference(row.estado_ptm)
        fingerprint.append([row.pk, row.inicio.isoformat(), str(row.valor), row.tipo, state])
        if state == "ERROR":
            continue
        if state == "EXITOSA" and ((row.tipo == "recarga" and row.valor > 0) or (row.tipo == "retiro" and row.valor < 0)):
            total += row.valor
            for ref in (row.id_tx, row.id_operador):
                if ref:
                    references[ref] = (row.tipo, abs(row.valor))
        else:
            warnings.append(f"Movimiento PTM {row.id_tx}: estado o producto pendiente de revisión.")
    for row in bag.ajustes.filter(creado_en__gte=start, creado_en__lt=end).order_by("pk"):
        total += row.monto
        fingerprint.append(["ajuste", row.pk, str(row.monto)])
    # Si el POS registró algo aún no observado, no se presume conciliado.
    pending = [op.pk for op in OperacionPTM.objects.filter(bolsa=bag, creado_en__gte=start, creado_en__lt=end).order_by("pk")
        if references.get(op.referencia) != (op.tipo, op.monto)]
    fingerprint.append(["sin_confirmar", pending])
    if pending:
        warnings.append("Hay operaciones del POS sin confirmación coincidente en la lectura PTM.")
    digest = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
    return total, warnings, digest


def reading_warnings(bag, start, end):
    now = timezone.now()
    first, last = start.astimezone(BOGOTA).date(), end.astimezone(BOGOTA).date()
    covered = {r.fecha: r.completada_en for r in LecturaDiariaPTM.objects.filter(
        cuenta_id=bag.cuenta_id, fecha__gte=first, fecha__lte=last)}
    day = first
    while day <= last:
        read_at = covered.get(day)
        if not read_at or read_at < end or (day == now.astimezone(BOGOTA).date() and read_at < now - timedelta(minutes=10)):
            return [f"Falta lectura completa posterior al conteo del {day.isoformat()}; el cuadre es provisional."]
        day += timedelta(days=1)
    return []


def ticket(turn, actor):
    online_only()
    if actor.pk != turn.cajero_id:
        raise ValidationError("El conteo de la bolsa debe hacerlo el cajero de este turno.")
    bag = bag_for_turn(turn)
    if not bag:
        raise ValidationError("La sucursal no tiene una bolsa PTM configurada.")
    membership = ParticipacionBolsaPTM.objects.filter(turno=turn).first()
    if membership and membership.salida:
        raise ValidationError("La bolsa de este turno ya fue entregada.")
    action = "salida" if membership else "entrada"
    required_state = "CIERRE" if membership else "ABIERTO"
    if turn.estado != required_state:
        raise ValidationError("Recibe la bolsa con el turno abierto; entrégala después de iniciar el cierre.")
    return signing.dumps({"bolsa": bag.pk, "version": bag.version, "turno": turn.pk,
        "actor": actor.pk, "accion": action, "desde_conteo": timezone.now().isoformat(),
        "solicitud": str(uuid4())}, salt=SALT)


@transaction.atomic
def count(*, actor, turn_id, counted, token):
    online_only()
    try:
        data = signing.loads(token, salt=SALT, max_age=600)
    except (signing.BadSignature, TypeError):
        raise ValidationError("El formulario venció. Vuelve a abrir el conteo PTM.")
    if data["actor"] != actor.pk or data["turno"] != turn_id:
        raise ValidationError("El formulario no corresponde a tu turno.")
    turn = TurnoCaja.objects.select_for_update().select_related("puntopago").get(pk=turn_id, cajero=actor)
    bag = bag_for_turn(turn)
    if not bag or bag.pk != data["bolsa"]:
        raise ValidationError("La bolsa del turno cambió.")
    bag = lock_bag(bag)
    amount = money(counted)
    previous = CorteBolsaPTM.objects.filter(solicitud=data["solicitud"]).first()
    if previous:
        if previous.turno_id != turn.pk or previous.contado != amount:
            raise ValidationError("El conteo ya fue confirmado con otros datos.")
        return previous
    if bag.version != data["version"]:
        raise ValidationError("Otra persona contó o ajustó la bolsa. Vuelve a contar desde un formulario nuevo.")
    membership = ParticipacionBolsaPTM.objects.filter(turno=turn).first()
    if (data["accion"] == "entrada" and (membership or turn.estado != "ABIERTO")) or (
        data["accion"] == "salida" and (not membership or membership.salida or turn.estado != "CIERRE")):
        raise ValidationError("El estado del turno o de la participación cambió.")
    prepared = parse_datetime(data["desde_conteo"])
    end = timezone.now()
    if MovimientoLeidoPTM.objects.filter(cuenta_id=bag.cuenta_id, inicio__gte=prepared, inicio__lt=end).exists():
        raise ValidationError("PTM tuvo movimientos durante el conteo. Detén las operaciones y vuelve a contar.")
    last = bag.cortes.order_by("-hasta", "-pk").first()
    start = last.hasta if last else bag.inicio
    base = last.contado if last else bag.base_inicial
    delta, warnings, digest = sources(bag, start, end)
    expected = money(base + delta, signed=True)
    difference = money(amount - expected, signed=True)
    active = list(bag.participaciones.filter(salida__isnull=True).values_list("turno_id", flat=True))
    if not active:
        warnings.append("Período sin participantes: revisión administrativa; no se carga al empleado entrante.")
    corte = CorteBolsaPTM.objects.create(bolsa=bag, turno=turn, actor=actor, accion=data["accion"],
        solicitud=data["solicitud"], desde=start, hasta=end, base=base, contado=amount,
        esperado=expected, diferencia=difference, firma_fuentes=digest,
        advertencias=warnings + reading_warnings(bag, start, end))
    corte.participantes.set(active)
    if data["accion"] == "entrada":
        ParticipacionBolsaPTM.objects.create(bolsa=bag, turno=turn, ingreso=end)
    else:
        membership.salida = end
        membership.save(update_fields=["salida"])
    bag.version += 1
    bag.save(update_fields=["version"])
    AuditoriaBolsaPTM.objects.create(bolsa=bag, corte=corte, actor=actor, accion="conteo",
        detalle={"contado": str(amount), "esperado": str(corte.esperado), "participantes": active})
    return corte


def refresh_cuts(account_id, changed_times):
    """Llamar dentro de la transacción del lector, que ya bloqueó su cuenta.

    Una llegada tardía corrige el corte original, no la base contada del siguiente.
    No se borran cortes ni se vuelven a emitir deudas por una relectura idéntica.
    """
    bag = BolsaEfectivoPTM.objects.select_for_update().filter(cuenta_id=account_id).first()
    if not bag or not changed_times:
        return
    for corte in bag.cortes.select_for_update().filter(desde__lte=max(changed_times), hasta__gt=min(changed_times)):
        delta, warnings, digest = sources(bag, corte.desde, corte.hasta)
        if digest == corte.firma_fuentes:
            continue
        before = str(corte.diferencia)
        corte.esperado = corte.base + delta
        corte.diferencia = corte.contado - corte.esperado
        corte.firma_fuentes = digest
        corte.revision += 1
        corte.estado = "provisional"
        corte.advertencias = warnings + ["La lectura PTM cambió después del conteo. Revisar este período, no trasladar su diferencia."]
        corte.save(update_fields=["esperado", "diferencia", "firma_fuentes", "revision", "estado", "advertencias"])
        AuditoriaBolsaPTM.objects.create(bolsa=bag, corte=corte, accion="lectura_tardia",
            detalle={"antes": before, "despues": str(corte.diferencia), "revision": corte.revision})


@transaction.atomic
def review(*, actor, cut_id, reason):
    admin_required(actor)
    cut = CorteBolsaPTM.objects.select_related("bolsa").get(pk=cut_id)
    bag = lock_bag(cut.bolsa)
    cut = CorteBolsaPTM.objects.select_for_update().get(pk=cut.pk)
    delta, warnings, digest = sources(bag, cut.desde, cut.hasta)
    if cut.esperado < 0 or warnings or reading_warnings(bag, cut.desde, cut.hasta) or digest != cut.firma_fuentes:
        raise ValidationError("Actualiza y concilia la lectura PTM antes de revisar este corte.")
    reason = str(reason or "").strip()
    if not 5 <= len(reason) <= 300:
        raise ValidationError("Escribe el motivo de la revisión (5 a 300 caracteres).")
    cut.estado = "revisado"
    cut.save(update_fields=["estado"])
    AuditoriaBolsaPTM.objects.create(bolsa=bag, corte=cut, actor=actor, accion="revision_admin", detalle={"motivo": reason})


def assert_received(turn):
    if bag_for_turn(turn) and not ParticipacionBolsaPTM.objects.filter(turno=turn).exists():
        raise ValidationError("Primero debes contar y recibir la bolsa PTM desde Turno de caja antes de iniciar el cierre.")


def assert_delivered(turn):
    membership = ParticipacionBolsaPTM.objects.filter(turno=turn, salida__isnull=True).first()
    if membership:
        raise ValidationError("Antes de cerrar caja debes contar y entregar la bolsa PTM compartida.")


def payload(turn, *, admin=False):
    """Lista blanca para cajeros: nunca esperado, contado de otros, ni sobrantes."""
    bag = bag_for_turn(turn)
    if not bag:
        return {"activa": False, "incidencias": []}
    membership = ParticipacionBolsaPTM.objects.filter(turno=turn).first()
    rows = CorteBolsaPTM.objects.filter(participantes=turn).distinct().prefetch_related("participantes__cajero")
    result = {"activa": True, "participa": bool(membership), "entregada": bool(membership and membership.salida),
        "conteo_url": reverse("bolsa_ptm_turno", args=[turn.pk]), "incidencias": [],
        "mensaje": "Conteo PTM registrado." if membership and membership.salida else "Bolsa PTM separada del efectivo de caja."}
    for cut in rows:
        if not admin and cut.diferencia >= 0:
            continue
        item = {"id": cut.pk, "faltante": str(max(-cut.diferencia, ZERO)), "estado": cut.estado,
            "desde": cut.desde.isoformat(), "hasta": cut.hasta.isoformat(),
            "participantes": [{"turno": t.pk, "nombre": t.cajero.nombreusuario} for t in cut.participantes.all()]}
        if admin:
            item.update(base=str(cut.base), esperado=str(cut.esperado), contado=str(cut.contado),
                diferencia=str(cut.diferencia), advertencias=cut.advertencias, revision=cut.revision)
        result["incidencias"].append(item)
    return result
