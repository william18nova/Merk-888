import ipaddress
import re
import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from mainApp.models import (
    AutorizacionDescuentoEspecial,
    ClienteEspecial,
)
from mainApp.permissions import is_web_master_role


SPECIAL_CLIENT_KEY = "merk2888"
SPECIAL_CODE_TTL = timedelta(minutes=15)
SPECIAL_CODE_LENGTH = 8
SPECIAL_CODE_MAX_ATTEMPTS = 5
MONEY_QUANTUM = Decimal("0.01")


class SpecialDiscountError(ValueError):
    def __init__(self, message, *, code="special_discount_error"):
        super().__init__(message)
        self.code = code


def _user_name(user):
    return (
        str(getattr(user, "nombreusuario", "") or "").strip()
        or str(user or "").strip()
        or "Usuario desconocido"
    )[:160]


def _safe_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return None


def _money(value, label):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise SpecialDiscountError(
            f"{label} no es un valor monetario válido.",
            code="invalid_amount",
        ) from exc
    if not amount.is_finite():
        raise SpecialDiscountError(
            f"{label} no es un valor monetario válido.",
            code="invalid_amount",
        )
    return amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _require_atomic():
    if not transaction.get_connection().in_atomic_block:
        raise SpecialDiscountError(
            "La autorización debe bloquearse y consumirse dentro de una transacción.",
            code="atomic_required",
        )


def _validate_code_format(code):
    normalized = str(code or "").strip()
    return normalized, bool(
        re.fullmatch(rf"[0-9]{{{SPECIAL_CODE_LENGTH}}}", normalized)
    )


def _ensure_available(authorization, now=None):
    now = now or timezone.now()
    if authorization.usada_en or authorization.venta_id:
        raise SpecialDiscountError(
            "Este código ya fue utilizado.",
            code="already_used",
        )
    if authorization.revocada_en:
        raise SpecialDiscountError(
            "Este código fue revocado.",
            code="revoked",
        )
    if authorization.bloqueada_en:
        raise SpecialDiscountError(
            "Este código está bloqueado por demasiados intentos fallidos.",
            code="blocked",
        )
    if authorization.intentos_fallidos >= SPECIAL_CODE_MAX_ATTEMPTS:
        raise SpecialDiscountError(
            "Este código está bloqueado por demasiados intentos fallidos.",
            code="blocked",
        )
    if authorization.expira_en <= now:
        raise SpecialDiscountError(
            "Este código ya expiró.",
            code="expired",
        )


def get_special_client_profile(cliente=None, for_update=False):
    queryset = ClienteEspecial.objects.select_related("cliente").filter(
        clave=SPECIAL_CLIENT_KEY,
        activo=True,
    )
    if cliente is not None:
        cliente_id = getattr(cliente, "pk", cliente)
        queryset = queryset.filter(cliente_id=cliente_id)
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.first()


def is_special_client(cliente):
    if cliente is None:
        return False
    return get_special_client_profile(cliente=cliente) is not None


def generate_one_time_code(generada_por, solicitud_id):
    if (
        not getattr(generada_por, "is_authenticated", False)
        or not getattr(generada_por, "is_active", False)
        or not is_web_master_role(generada_por)
    ):
        raise SpecialDiscountError(
            "Solo un usuario con rol Web Master puede generar este código.",
            code="web_master_required",
        )

    solicitud_id = str(solicitud_id or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{32}", solicitud_id):
        raise SpecialDiscountError(
            "La solicitud de generación no es válida. Recarga la página.",
            code="invalid_request",
        )

    now = timezone.now()
    with transaction.atomic():
        profile = get_special_client_profile(for_update=True)
        if not profile:
            raise SpecialDiscountError(
                "El cliente especial merk2888 no está configurado o está inactivo.",
                code="special_client_unavailable",
            )

        if AutorizacionDescuentoEspecial.objects.filter(
            solicitud_id=solicitud_id,
        ).exists():
            raise SpecialDiscountError(
                "Esta solicitud ya fue procesada. La clave vigente no cambió.",
                code="duplicate_request",
            )

        if AutorizacionDescuentoEspecial.objects.filter(
            cliente_especial=profile,
            usada_en__isnull=True,
            revocada_en__isnull=True,
            bloqueada_en__isnull=True,
            intentos_fallidos__lt=SPECIAL_CODE_MAX_ATTEMPTS,
            expira_en__gt=now,
        ).exists():
            raise SpecialDiscountError(
                "Ya existe una clave vigente. Revócala antes de generar otra.",
                code="active_code_exists",
            )

        (
            AutorizacionDescuentoEspecial.objects
            .select_for_update()
            .filter(
                cliente_especial=profile,
                usada_en__isnull=True,
                revocada_en__isnull=True,
            )
            .update(
                revocada_en=now,
                revocada_por=generada_por,
                revocada_por_nombre=_user_name(generada_por),
            )
        )

        for _ in range(20):
            code = f"{secrets.randbelow(10 ** SPECIAL_CODE_LENGTH):0{SPECIAL_CODE_LENGTH}d}"
            # Identificador aleatorio, sin relación matemática con la clave.
            # Así, aun teniendo acceso a la base de datos, no se puede probar
            # rápidamente el espacio de 8 dígitos contra este campo.
            selector = secrets.token_hex(32)
            reference = f"MERK-{secrets.token_hex(4).upper()}"
            if AutorizacionDescuentoEspecial.objects.filter(
                selector=selector,
            ).exists():
                continue
            if AutorizacionDescuentoEspecial.objects.filter(
                referencia=reference,
            ).exists():
                continue
            break
        else:
            raise SpecialDiscountError(
                "No fue posible generar un código único. Intenta nuevamente.",
                code="code_generation_failed",
            )

        authorization = AutorizacionDescuentoEspecial.objects.create(
            cliente_especial=profile,
            selector=selector,
            referencia=reference,
            solicitud_id=solicitud_id,
            secreto_hash=make_password(code),
            generada_por=generada_por,
            generada_por_nombre=_user_name(generada_por),
            generada_en=now,
            expira_en=now + SPECIAL_CODE_TTL,
        )

    return authorization, code


def preview_one_time_code(
    cliente,
    codigo,
    *,
    actor=None,
    ip_address=None,
):
    error = None
    authorization = None

    with transaction.atomic():
        profile = get_special_client_profile(
            cliente=cliente,
            for_update=True,
        )
        if not profile:
            raise SpecialDiscountError(
                "El código solo puede usarse con el cliente especial merk2888.",
                code="wrong_client",
            )

        authorization = (
            AutorizacionDescuentoEspecial.objects
            .select_for_update()
            .filter(
                cliente_especial=profile,
                usada_en__isnull=True,
                revocada_en__isnull=True,
            )
            .order_by("-generada_en", "-pk")
            .first()
        )
        if not authorization:
            raise SpecialDiscountError(
                "No hay un código disponible para el cliente especial.",
                code="no_active_code",
            )

        _ensure_available(authorization)
        normalized, valid_format = _validate_code_format(codigo)
        valid_secret = (
            valid_format
            and check_password(
                normalized,
                authorization.secreto_hash,
            )
        )
        if not valid_secret:
            failed_at = timezone.now()
            authorization.intentos_fallidos += 1
            authorization.ultimo_intento_fallido_en = failed_at
            authorization.ultimo_intento_fallido_por = (
                actor if getattr(actor, "pk", None) else None
            )
            authorization.ultimo_intento_fallido_por_nombre = _user_name(actor)
            authorization.ultimo_intento_fallido_ip = _safe_ip(ip_address)
            update_fields = [
                "intentos_fallidos",
                "ultimo_intento_fallido_en",
                "ultimo_intento_fallido_por",
                "ultimo_intento_fallido_por_nombre",
                "ultimo_intento_fallido_ip",
            ]
            if authorization.intentos_fallidos >= SPECIAL_CODE_MAX_ATTEMPTS:
                authorization.bloqueada_en = failed_at
                update_fields.append("bloqueada_en")
                error = SpecialDiscountError(
                    "El código fue bloqueado por demasiados intentos fallidos.",
                    code="blocked",
                )
            else:
                remaining = (
                    SPECIAL_CODE_MAX_ATTEMPTS
                    - authorization.intentos_fallidos
                )
                error = SpecialDiscountError(
                    f"El código no es correcto. Quedan {remaining} intentos.",
                    code="invalid_code",
                )
            authorization.save(update_fields=update_fields)

    if error:
        raise error
    return authorization


def lock_one_time_code(cliente, codigo, autorizacion_id):
    _require_atomic()

    profile = get_special_client_profile(
        cliente=cliente,
        for_update=True,
    )
    if not profile:
        raise SpecialDiscountError(
            "El código solo puede usarse con el cliente especial merk2888.",
            code="wrong_client",
        )

    authorization = (
        AutorizacionDescuentoEspecial.objects
        .select_for_update()
        .filter(
            pk=autorizacion_id,
            cliente_especial=profile,
        )
        .first()
    )
    if not authorization:
        raise SpecialDiscountError(
            "La autorización indicada no existe o no pertenece a este cliente.",
            code="authorization_not_found",
        )

    _ensure_available(authorization)
    normalized, valid_format = _validate_code_format(codigo)
    if (
        not valid_format
        or not check_password(normalized, authorization.secreto_hash)
    ):
        raise SpecialDiscountError(
            "El código no es correcto.",
            code="invalid_code",
        )
    return authorization


def consume_one_time_code(
    autorizacion,
    venta,
    usada_por,
    turno,
    sucursal,
    subtotal,
    descuento,
    turno_requerido=True,
):
    _require_atomic()
    if not getattr(autorizacion, "pk", None):
        raise SpecialDiscountError(
            "La autorización no es válida.",
            code="authorization_not_found",
        )
    if not getattr(venta, "pk", None):
        raise SpecialDiscountError(
            "La venta debe existir antes de consumir el código.",
            code="sale_required",
        )

    authorization = (
        AutorizacionDescuentoEspecial.objects
        .select_related("cliente_especial")
        .select_for_update()
        .get(pk=autorizacion.pk)
    )
    _ensure_available(authorization)

    if venta.clienteid_id != authorization.cliente_especial.cliente_id:
        raise SpecialDiscountError(
            "La venta no pertenece al cliente especial autorizado.",
            code="wrong_client",
        )
    if sucursal is None or venta.sucursalid_id != getattr(sucursal, "pk", sucursal):
        raise SpecialDiscountError(
            "La sucursal de la autorización no coincide con la venta.",
            code="wrong_branch",
        )
    if turno_requerido and turno is None:
        raise SpecialDiscountError(
            "La venta debe tener un turno de caja activo.",
            code="shift_required",
        )
    if turno is not None and (
        str(getattr(turno, "estado", "") or "").strip().upper() != "ABIERTO"
    ):
        raise SpecialDiscountError(
            "El turno de caja ya no está abierto.",
            code="shift_not_open",
        )
    if turno is not None and (
        getattr(turno, "cajero_id", None) != getattr(usada_por, "pk", None)
    ):
        raise SpecialDiscountError(
            "El turno no pertenece al cajero que registra la venta.",
            code="wrong_cashier",
        )
    if turno is not None and (
        getattr(turno, "puntopago_id", None) != venta.puntopagoid_id
    ):
        raise SpecialDiscountError(
            "El punto de pago del turno no coincide con la venta.",
            code="wrong_payment_point",
        )

    subtotal_amount = _money(subtotal, "El subtotal")
    discount_amount = _money(descuento, "El descuento")
    sale_total = _money(venta.total, "El total de la venta")
    if subtotal_amount <= 0:
        raise SpecialDiscountError(
            "El subtotal autorizado debe ser mayor que cero.",
            code="invalid_subtotal",
        )
    if discount_amount != subtotal_amount or sale_total != Decimal("0.00"):
        raise SpecialDiscountError(
            "Esta autorización exige un descuento del 100% y una venta en cero.",
            code="invalid_discount",
        )

    now = timezone.now()
    authorization.usada_en = now
    authorization.usada_por = usada_por
    authorization.usada_por_nombre = _user_name(usada_por)
    authorization.venta = venta
    authorization.turno = turno
    authorization.sucursal = sucursal
    authorization.subtotal_aplicado = subtotal_amount
    authorization.descuento_aplicado = discount_amount
    authorization.save(
        update_fields=[
            "usada_en",
            "usada_por",
            "usada_por_nombre",
            "venta",
            "turno",
            "sucursal",
            "subtotal_aplicado",
            "descuento_aplicado",
        ]
    )
    return authorization
