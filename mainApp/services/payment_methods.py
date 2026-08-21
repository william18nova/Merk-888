import re
import unicodedata
from typing import Iterable

from django.db import connection


CASH_PAYMENT_CODE = "efectivo"
NEQUI_PAYMENT_CODE = "nequi"
INTERNAL_PAYMENT_CODES = frozenset({"mixto", "sin_pago", "facturas_pagadas"})
PAYMENT_METHOD_CODE_MAX_LENGTH = 50

# Se usa como respaldo antes de aplicar la migración y como fuente de la
# migración de datos inicial. Los códigos son contratos históricos del sistema.
DEFAULT_PAYMENT_METHODS = (
    {
        "code": "efectivo",
        "label": "Efectivo",
        "active": True,
        "is_cash": True,
        "is_system": True,
        "order": 10,
        "version": 1,
        "updated_at": None,
        "updated_by": "",
    },
    {
        "code": "nequi",
        "label": "Nequi",
        "active": True,
        "is_cash": False,
        "is_system": True,
        "order": 20,
        "version": 1,
        "updated_at": None,
        "updated_by": "",
    },
    {
        "code": "daviplata",
        "label": "Daviplata",
        "active": True,
        "is_cash": False,
        "is_system": True,
        "order": 30,
        "version": 1,
        "updated_at": None,
        "updated_by": "",
    },
    {
        "code": "tarjeta",
        "label": "Tarjeta",
        "active": True,
        "is_cash": False,
        "is_system": True,
        "order": 40,
        "version": 1,
        "updated_at": None,
        "updated_by": "",
    },
    {
        "code": "banco_caja_social",
        "label": "Banco Caja Social",
        "active": True,
        "is_cash": False,
        "is_system": True,
        "order": 50,
        "version": 1,
        "updated_at": None,
        "updated_by": "",
    },
)

_LEGACY_ALIASES = {
    "ef": "efectivo",
    "cash": "efectivo",
    "davi": "daviplata",
    "davi_plata": "daviplata",
    "card": "tarjeta",
    "tc": "tarjeta",
    "credito": "tarjeta",
    "debito": "tarjeta",
    "tarjeta_credito": "tarjeta",
    "tarjeta_debito": "tarjeta",
    "caja_social": "banco_caja_social",
    "bcs": "banco_caja_social",
}

SYSTEM_PAYMENT_CODES = frozenset(
    row["code"] for row in DEFAULT_PAYMENT_METHODS
)
RESERVED_PAYMENT_METHOD_CODES = frozenset(
    set(INTERNAL_PAYMENT_CODES)
    | set(SYSTEM_PAYMENT_CODES)
    | set(_LEGACY_ALIASES)
)


class PaymentMethodCodeError(ValueError):
    """Un codigo nuevo no cumple el contrato del catalogo."""


class PaymentMethodConfigurationError(RuntimeError):
    """El catalogo contiene un codigo que no puede usarse con seguridad."""


def _slug_payment_method_code(value) -> str:
    text = str(value or "").strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def normalize_payment_method_code(value) -> str:
    """Normaliza lectura historica; los aliases regresan su codigo canonico."""

    token = _slug_payment_method_code(value)
    return _LEGACY_ALIASES.get(token, token)


def validate_new_payment_method_code(value) -> str:
    """
    Valida el codigo de un metodo creado por el usuario.

    Los aliases historicos no se convierten silenciosamente: ``cash`` no puede
    terminar modificando ``efectivo``. Los codigos internos tampoco forman
    parte del catalogo porque representan estados derivados del sistema.
    """

    token = _slug_payment_method_code(value)
    if not token:
        raise PaymentMethodCodeError("Escribe un codigo para el metodo de pago.")
    if len(token) > PAYMENT_METHOD_CODE_MAX_LENGTH:
        raise PaymentMethodCodeError(
            f"El codigo no puede superar {PAYMENT_METHOD_CODE_MAX_LENGTH} caracteres."
        )
    if token in INTERNAL_PAYMENT_CODES:
        raise PaymentMethodCodeError(
            f"El codigo '{token}' es interno y esta reservado por el sistema."
        )
    if token in _LEGACY_ALIASES:
        canonical = _LEGACY_ALIASES[token]
        raise PaymentMethodCodeError(
            f"El codigo '{token}' es un alias reservado de '{canonical}'."
        )
    if token in SYSTEM_PAYMENT_CODES:
        raise PaymentMethodCodeError(
            f"El codigo '{token}' pertenece a un metodo del sistema."
        )
    return token


def validate_active_payment_method_code(value) -> str:
    """Retorna el codigo canonico solo si esta activo para nuevos movimientos."""

    code = normalize_payment_method_code(value)
    if (
        not code
        or len(code) > PAYMENT_METHOD_CODE_MAX_LENGTH
        or code in INTERNAL_PAYMENT_CODES
        or code not in active_payment_method_codes()
    ):
        raise PaymentMethodCodeError(
            f"Medio de pago no valido o inactivo: {code or 'vacio'}."
        )
    return code


def payment_method_label_from_code(code) -> str:
    normalized = normalize_payment_method_code(code)
    if not normalized:
        return "Sin pago"
    return normalized.replace("_", " ").title()


def _normalized_include_codes(values: Iterable | None) -> list[str]:
    seen = set()
    output = []
    for raw in values or ():
        code = normalize_payment_method_code(raw)
        if (
            code
            and len(code) <= PAYMENT_METHOD_CODE_MAX_LENGTH
            and code not in seen
            and code not in INTERNAL_PAYMENT_CODES
        ):
            seen.add(code)
            output.append(code)
    return output


def _fallback_options(*, active_only=True, include_codes=None):
    include = _normalized_include_codes(include_codes)
    rows = [dict(row) for row in DEFAULT_PAYMENT_METHODS]
    if active_only:
        rows = [row for row in rows if row["active"] or row["code"] in include]

    known = {row["code"] for row in rows}
    for code in include:
        if code not in known:
            rows.append({
                "code": code,
                "label": payment_method_label_from_code(code),
                "active": False,
                "is_cash": code == CASH_PAYMENT_CODE,
                "is_system": False,
                "order": 999,
                "version": 0,
                "updated_at": None,
                "updated_by": "",
            })
    return rows


def _database_access_is_forbidden(error: AssertionError) -> bool:
    """Reconoce exclusivamente el bloqueo intencional de ``SimpleTestCase``."""

    cls = error.__class__
    return (
        cls.__name__ == "DatabaseOperationForbidden"
        and cls.__module__ == "django.test.testcases"
    )


def _catalog_code(stored_code) -> str:
    """Exige que lo persistido ya sea canonico; no corrige corrupcion en vivo."""

    stored = str(stored_code or "").strip()
    normalized = normalize_payment_method_code(stored)
    if (
        not normalized
        or len(normalized) > PAYMENT_METHOD_CODE_MAX_LENGTH
        or normalized in INTERNAL_PAYMENT_CODES
        or stored != normalized
    ):
        raise PaymentMethodConfigurationError(
            f"Codigo inseguro en el catalogo de metodos de pago: {stored or 'vacio'}."
        )
    return normalized


def payment_method_options(*, active_only=True, include_codes=None):
    """Devuelve dicts estables para plantillas y validaciones de dominio."""

    include = _normalized_include_codes(include_codes)
    if not payment_method_table_ready():
        return _fallback_options(
            active_only=active_only,
            include_codes=include,
        )

    from mainApp.models import MetodoPago

    queryset = MetodoPago.objects.all()
    if active_only:
        from django.db.models import Q

        queryset = queryset.filter(Q(activo=True) | Q(codigo__in=include))

    rows = []
    for row in queryset.order_by("orden", "nombre", "codigo"):
        code = _catalog_code(row.codigo)
        rows.append({
            "code": code,
            "label": (row.nombre or "").strip() or payment_method_label_from_code(code),
            "active": bool(row.activo),
            "is_cash": bool(row.es_efectivo),
            "is_system": bool(row.es_sistema),
            "order": int(row.orden or 0),
            "version": int(row.version or 1),
            "updated_at": row.actualizado_en,
            "updated_by": row.actualizado_por_nombre,
        })

    known = {row["code"] for row in rows}
    for code in include:
        if code not in known:
            rows.append({
                "code": code,
                "label": payment_method_label_from_code(code),
                "active": False,
                "is_cash": code == CASH_PAYMENT_CODE,
                "is_system": False,
                "order": 999,
                "version": 0,
                "updated_at": None,
                "updated_by": "",
            })
    return rows


def active_payment_method_codes() -> set[str]:
    return {
        row["code"]
        for row in payment_method_options(active_only=True)
        if row["active"]
    }


def all_payment_method_codes() -> set[str]:
    return {
        row["code"]
        for row in payment_method_options(active_only=False)
    }


def payment_method_choices(*, active_only=True, include_codes=None):
    return [
        (row["code"], row["label"])
        for row in payment_method_options(
            active_only=active_only,
            include_codes=include_codes,
        )
    ]


def payment_method_label_map(*, include_codes=None) -> dict[str, str]:
    return {
        row["code"]: row["label"]
        for row in payment_method_options(
            active_only=False,
            include_codes=include_codes,
        )
    }


def payment_method_label(code, *, labels=None) -> str:
    normalized = normalize_payment_method_code(code)
    mapping = labels if labels is not None else payment_method_label_map(
        include_codes=[normalized],
    )
    return mapping.get(normalized) or payment_method_label_from_code(normalized)


def payment_method_table_ready() -> bool:
    try:
        return "metodos_pago" in connection.introspection.table_names()
    except AssertionError as error:
        # SimpleTestCase prohibe cualquier consulta. Para esos tests puros el
        # respaldo estatico representa exactamente el estado previo a 0028.
        if _database_access_is_forbidden(error):
            return False
        raise
