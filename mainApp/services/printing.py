from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from django.core.cache import cache
from django.db import DatabaseError, transaction

from mainApp.models import ConfiguracionImpresion


SISTEMA_WINDOWS: Final = "windows"
SISTEMA_LINUX: Final = "linux"
TAMANO_GRANDE: Final = "grande"
TAMANO_PEQUENA: Final = "pequena"

SISTEMAS_OPERATIVOS: Final = frozenset({SISTEMA_WINDOWS, SISTEMA_LINUX})
TAMANOS_FACTURA: Final = frozenset({TAMANO_GRANDE, TAMANO_PEQUENA})


@dataclass(frozen=True, slots=True)
class PrintProfile:
    sistema_operativo: str
    tamano_factura: str
    width_chars: int
    cups_media: str


_PRINT_PROFILES = MappingProxyType(
    {
        (SISTEMA_WINDOWS, TAMANO_GRANDE): PrintProfile(
            sistema_operativo=SISTEMA_WINDOWS,
            tamano_factura=TAMANO_GRANDE,
            width_chars=48,
            cups_media="Custom.80x60mm",
        ),
        (SISTEMA_WINDOWS, TAMANO_PEQUENA): PrintProfile(
            sistema_operativo=SISTEMA_WINDOWS,
            tamano_factura=TAMANO_PEQUENA,
            width_chars=32,
            cups_media="Custom.58x3276mm",
        ),
        (SISTEMA_LINUX, TAMANO_GRANDE): PrintProfile(
            sistema_operativo=SISTEMA_LINUX,
            tamano_factura=TAMANO_GRANDE,
            width_chars=48,
            cups_media="Custom.80x60mm",
        ),
        (SISTEMA_LINUX, TAMANO_PEQUENA): PrintProfile(
            sistema_operativo=SISTEMA_LINUX,
            tamano_factura=TAMANO_PEQUENA,
            width_chars=32,
            cups_media="Custom.58x3276mm",
        ),
    }
)
PRINT_PROFILES = _PRINT_PROFILES
DEFAULT_PRINT_PROFILE: Final = _PRINT_PROFILES[(SISTEMA_WINDOWS, TAMANO_GRANDE)]
PRINT_PROFILE_CACHE_SECONDS: Final = 3


def normalize_sistema_operativo(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("El sistema operativo debe ser texto.")
    normalized = value.strip().lower()
    if normalized not in SISTEMAS_OPERATIVOS:
        raise ValueError("Sistema operativo de impresión no válido.")
    return normalized


def normalize_tamano_factura(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("El tamaño de factura debe ser texto.")
    normalized = value.strip().lower()
    if normalized not in TAMANOS_FACTURA:
        raise ValueError("Tamaño de factura no válido.")
    return normalized


def resolve_print_profile(
    sistema_operativo: object,
    tamano_factura: object,
) -> PrintProfile:
    key = (
        normalize_sistema_operativo(sistema_operativo),
        normalize_tamano_factura(tamano_factura),
    )
    return _PRINT_PROFILES[key]


def _punto_pago_id(punto_pago: object) -> object:
    if punto_pago is None or isinstance(punto_pago, bool):
        return None
    return getattr(punto_pago, "pk", punto_pago)


def _profile_cache_key(punto_pago_id: object) -> str:
    return f"mainapp:print-profile:{punto_pago_id}"


def clear_print_profile_cache(punto_pago: object) -> None:
    punto_pago_id = _punto_pago_id(punto_pago)
    if punto_pago_id not in (None, ""):
        cache.delete(_profile_cache_key(punto_pago_id))


def get_print_profile(punto_pago: object, *, fresh: bool = False) -> PrintProfile:
    """
    Obtiene el perfil del punto de pago sin hacer obligatoria la migración.

    El bloque atómico interno crea un savepoint cuando el llamador ya está en
    una transacción. Si la tabla todavía no existe, el error se revierte antes
    de devolver el perfil seguro y no deja rota la transacción exterior.
    """
    punto_pago_id = _punto_pago_id(punto_pago)
    if punto_pago_id in (None, ""):
        return DEFAULT_PRINT_PROFILE

    cache_key = _profile_cache_key(punto_pago_id)
    if not fresh:
        cached = cache.get(cache_key)
        if isinstance(cached, (tuple, list)) and len(cached) == 2:
            try:
                return resolve_print_profile(*cached)
            except ValueError:
                cache.delete(cache_key)

    try:
        with transaction.atomic():
            selection = (
                ConfiguracionImpresion.objects
                .filter(punto_pago_id=punto_pago_id)
                .values_list("sistema_operativo", "tamano_factura")
                .first()
            )
    except (DatabaseError, TypeError, ValueError):
        cache.set(
            cache_key,
            (
                DEFAULT_PRINT_PROFILE.sistema_operativo,
                DEFAULT_PRINT_PROFILE.tamano_factura,
            ),
            PRINT_PROFILE_CACHE_SECONDS,
        )
        return DEFAULT_PRINT_PROFILE

    if not selection:
        profile = DEFAULT_PRINT_PROFILE
    else:
        try:
            profile = resolve_print_profile(*selection)
        except ValueError:
            profile = DEFAULT_PRINT_PROFILE

    cache.set(
        cache_key,
        (profile.sistema_operativo, profile.tamano_factura),
        PRINT_PROFILE_CACHE_SECONDS,
    )
    return profile
