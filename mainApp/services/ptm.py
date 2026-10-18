"""Registro PTM local. No ejecuta ni valida operaciones en la plataforma externa."""

import re
import unicodedata
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum, F

from mainApp.models import ConteoCierrePTM, OperacionPTM, Producto, PuntosPago, TurnoCaja


def resumen_ptm(turno):
    result = {"cantidad": 0, "retiros_cantidad": 0, "recargas_cantidad": 0,
              "retiros": Decimal("0.00"), "recargas": Decimal("0.00")}
    for row in OperacionPTM.objects.filter(turno=turno).order_by().values("tipo").annotate(
        cantidad=Count("pk"), monto=Sum("monto"),
    ):
        key = "retiros" if row["tipo"] == "retiro" else "recargas"
        result[key] = row["monto"]
        result[key + "_cantidad"] = row["cantidad"]
        result["cantidad"] += row["cantidad"]
    result["neto"] = result["recargas"] - result["retiros"]
    return result


def resumen_ptm_json(turno):
    result = resumen_ptm(turno)
    result = {key: str(value) if isinstance(value, Decimal) else value for key, value in result.items()}
    ultimo = ConteoCierrePTM.objects.filter(turno=turno).first()
    result["declarado"] = ultimo.declarado if ultimo else None
    return result


def validar_conteo_ptm(turno, actor, valor):
    """El llamador mantiene bloqueado el turno hasta finalizar el cierre."""
    registrado = resumen_ptm(turno)["cantidad"]
    text = str(valor if valor is not None else "").strip() or "0"
    if not re.fullmatch(r"[0-9]{1,8}", text):
        raise ValidationError("Escribe el número de transacciones PTM del turno (0 si no hubo).")
    declarado = int(text)
    if not registrado and not declarado:
        return
    ConteoCierrePTM.objects.create(
        turno=turno, usuario=actor, declarado=declarado, registrado=registrado,
    )
    if declarado != registrado:
        raise ValidationError(
            "El número de transacciones PTM no coincide. Revisa los comprobantes y el registro PTM "
            "antes de cerrar. No ajustes el efectivo ni las facturas para compensarlo."
        )


@transaction.atomic
def registrar_operacion_ptm(*, actor, turno_id, tipo, monto, referencia, solicitud_id):
    if tipo not in {"retiro", "recarga"}:
        raise ValidationError("Selecciona retiro o recarga/pago.")
    try:
        amount = Decimal(str(monto))
        if (not amount.is_finite() or amount <= 0 or amount > Decimal("9999999999.99")
                or amount != amount.quantize(Decimal("0.01"))):
            raise ValueError
        request_id = UUID(str(solicitud_id))
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        raise ValidationError("Revisa el monto y vuelve a cargar el formulario si es necesario.")
    reference = "".join(unicodedata.normalize("NFKC", str(referencia or "")).upper().split())
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9._/-]{2,99}", reference):
        raise ValidationError("Escribe la referencia real del comprobante PTM (de 3 a 100 caracteres).")
    # El mismo bloqueo utilizado al iniciar/cerrar caja evita operaciones tardías.
    turn = TurnoCaja.objects.select_for_update().filter(pk=turno_id, cajero=actor).first()
    if not turn:
        raise ValidationError("Selecciona tu propio turno de caja.")
    previous = OperacionPTM.objects.filter(solicitud_id=request_id).first()
    if previous:
        if (previous.turno_id == turn.pk and previous.usuario_id == actor.pk
                and previous.tipo == tipo and previous.monto == amount and previous.referencia == reference):
            return previous, False
        raise ValidationError("Esta solicitud ya se utilizó con otros datos. Recarga la página.")
    if turn.estado != "ABIERTO":
        raise ValidationError("PTM requiere un turno ABIERTO. No puedes registrar durante o después del cierre.")
    product = Producto.objects.filter(tipo_ptm=tipo).first()
    if not product:
        raise ValidationError("Faltan los productos PTM. Aplica la migración 0040.")
    try:
        with transaction.atomic():
            operation = OperacionPTM.objects.create(
                turno=turn, usuario=actor, producto=product, tipo=tipo, monto=amount,
                referencia=reference, solicitud_id=request_id,
            )
    except IntegrityError:
        raise ValidationError("La referencia PTM ya está registrada. Comprueba el historial antes de repetirla.")
    # Mantener también el saldo global que usa el resto de operaciones de caja.
    # El cuadre calcula su propio esperado desde el ledger, sin sumar este saldo.
    delta = -amount if tipo == "retiro" else amount
    PuntosPago.objects.filter(pk=turn.puntopago_id).update(dinerocaja=F("dinerocaja") + delta)
    return operation, True
