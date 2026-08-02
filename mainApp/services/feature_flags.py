from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from django.core.cache import cache
from django.db import DatabaseError, transaction


TURN_REQUIRED_FEATURE = "ventas_exigir_turno_caja"
FEATURE_CACHE_SECONDS = 3


FEATURE_REGISTRY = {
    TURN_REQUIRED_FEATURE: {
        "key": TURN_REQUIRED_FEATURE,
        "category": "Caja y ventas",
        "label": "Exigir turno de caja para vender",
        "description": (
            "Define si cada cajero debe abrir un turno antes de generar ventas."
        ),
        "enabled_help": (
            "Cada venta exige un turno abierto del mismo cajero y punto de pago. "
            "Se conservan apertura, cierre y cuadre individual."
        ),
        "disabled_help": (
            "Los cajeros venden directamente desde su sucursal asignada y eligen "
            "el punto de pago. No habrá apertura, cierre ni cuadre individual."
        ),
        "default_enabled": True,
        "critical": True,
    },
}


# Al apagar el inicio obligatorio se ocultan y bloquean únicamente las
# operaciones manuales vinculadas a la apertura, cierre o retiro de un turno.
# Los dashboards y APIs de cierre permanecen disponibles para consultar o
# sanear históricos.
FEATURE_ROUTES = {
    TURN_REQUIRED_FEATURE: {
        "turno_caja",
        "turno_recuperar_o_iniciar",
        "turno_caja_puntopago_ac",
        "turno_caja_cajero_ac",
        "turno_caja_iniciar",
        "turno_caja_retiro_actual",
        "turno_caja_retiro",
    },
}


class FeatureFlagError(ValueError):
    def __init__(self, message, *, code="feature_error"):
        super().__init__(message)
        self.code = code


class ActiveCashTurnError(FeatureFlagError):
    def __init__(self, count):
        super().__init__(
            (
                "No se puede desactivar mientras existan turnos abiertos o en "
                f"cierre. Primero cierra los {count} turno(s) pendiente(s)."
            ),
            code="active_cash_turns",
        )
        self.count = int(count)


@dataclass(frozen=True)
class FeatureChangeResult:
    changed: bool
    duplicate: bool
    enabled: bool
    version: int


def feature_definition(key):
    definition = FEATURE_REGISTRY.get(str(key or "").strip())
    if not definition:
        raise FeatureFlagError(
            "La funcionalidad indicada no está registrada.",
            code="unknown_feature",
        )
    return definition


def _cache_key(key):
    return f"mainapp:feature:{key}"


def clear_feature_cache(key=None):
    if key:
        cache.delete(_cache_key(key))
        return
    cache.delete_many([_cache_key(name) for name in FEATURE_REGISTRY])


def is_feature_enabled(key, *, fresh=False):
    """Lectura tolerante para navegación; falla al estado seguro declarado."""

    definition = feature_definition(key)
    if not fresh:
        cached = cache.get(_cache_key(key))
        if cached is not None:
            return bool(cached)

    try:
        from mainApp.models import ConfiguracionFuncionalidad

        value = (
            ConfiguracionFuncionalidad.objects
            .filter(pk=key)
            .values_list("habilitada", flat=True)
            .first()
        )
    except DatabaseError:
        # Durante un despliegue en el que aún falta la migración, conservar el
        # comportamiento histórico evita habilitar ventas sin turno por error.
        return bool(definition["default_enabled"])

    enabled = (
        bool(definition["default_enabled"])
        if value is None
        else bool(value)
    )
    cache.set(_cache_key(key), enabled, FEATURE_CACHE_SECONDS)
    return enabled


def feature_for_url(url_name) -> Optional[str]:
    target = str(url_name or "")
    for key, route_names in FEATURE_ROUTES.items():
        if target in route_names:
            return key
    return None


def disabled_feature_for_url(url_name, *, fresh=False):
    key = feature_for_url(url_name)
    if not key or is_feature_enabled(key, fresh=fresh):
        return None
    return feature_definition(key)


def feature_rows():
    """Devuelve el registro completo usando la BD como autoridad."""

    try:
        from mainApp.models import ConfiguracionFuncionalidad

        rows = {
            row.clave: row
            for row in ConfiguracionFuncionalidad.objects.filter(
                clave__in=FEATURE_REGISTRY,
            ).select_related("actualizada_por")
        }
    except DatabaseError:
        rows = {}

    output = []
    for key, definition in FEATURE_REGISTRY.items():
        row = rows.get(key)
        output.append({
            **definition,
            "enabled": (
                bool(row.habilitada)
                if row is not None
                else bool(definition["default_enabled"])
            ),
            "version": int(getattr(row, "version", 1) or 1),
            "updated_at": getattr(row, "actualizada_en", None),
            "updated_by": (
                getattr(row, "actualizada_por_nombre", "")
                if row is not None
                else ""
            ),
        })
    return output


def _actor_name(actor):
    return (
        getattr(actor, "nombreusuario", "")
        or getattr(actor, "username", "")
        or str(actor)
    )[:160]


def locked_feature_enabled(key):
    """Lee y bloquea el flag. Debe ejecutarse dentro de transaction.atomic."""

    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "La configuración crítica debe bloquearse dentro de una transacción."
        )

    definition = feature_definition(key)
    from mainApp.models import ConfiguracionFuncionalidad

    row = (
        ConfiguracionFuncionalidad.objects
        .select_for_update()
        .filter(pk=key)
        .first()
    )
    if row is None:
        row = ConfiguracionFuncionalidad.objects.create(
            clave=key,
            habilitada=bool(definition["default_enabled"]),
            version=1,
        )
        row = (
            ConfiguracionFuncionalidad.objects
            .select_for_update()
            .get(pk=row.pk)
        )
    return bool(row.habilitada)


def set_feature_enabled(
    *,
    key,
    enabled,
    expected_version,
    actor,
    reason,
    request_id,
    ip=None,
    user_agent="",
):
    """Cambia un flag de forma serializada, auditable e idempotente."""

    definition = feature_definition(key)
    try:
        request_uuid = UUID(str(request_id or ""))
    except (TypeError, ValueError, AttributeError):
        raise FeatureFlagError(
            "La solicitud de cambio no es válida. Recarga la página.",
            code="invalid_request_id",
        )

    reason = str(reason or "").strip()
    enabled = bool(enabled)
    if not enabled and definition.get("critical") and len(reason) < 8:
        raise FeatureFlagError(
            "Explica brevemente por qué vas a desactivar esta función.",
            code="reason_required",
        )

    from mainApp.models import (
        CambioConfiguracionFuncionalidad,
        ConfiguracionFuncionalidad,
        TurnoCaja,
    )

    with transaction.atomic():
        duplicate = (
            CambioConfiguracionFuncionalidad.objects
            .filter(solicitud_id=request_uuid)
            .select_related("funcionalidad")
            .first()
        )
        if duplicate:
            duplicate_key = getattr(
                duplicate,
                "funcionalidad_id",
                getattr(
                    getattr(duplicate, "funcionalidad", None),
                    "pk",
                    key,
                ),
            )
            if duplicate_key != key:
                raise FeatureFlagError(
                    "La solicitud ya fue usada para otra funcionalidad. "
                    "Recarga la página.",
                    code="invalid_request_id",
                )
            return FeatureChangeResult(
                changed=False,
                duplicate=True,
                enabled=bool(duplicate.nuevo),
                version=int(duplicate.funcionalidad.version),
            )

        row = (
            ConfiguracionFuncionalidad.objects
            .select_for_update()
            .filter(pk=key)
            .first()
        )
        if row is None:
            row = ConfiguracionFuncionalidad.objects.create(
                clave=key,
                habilitada=bool(definition["default_enabled"]),
                version=1,
            )
            row = (
                ConfiguracionFuncionalidad.objects
                .select_for_update()
                .get(pk=row.pk)
            )

        try:
            expected_version = int(expected_version)
        except (TypeError, ValueError):
            raise FeatureFlagError(
                "La página quedó desactualizada. Recárgala e intenta nuevamente.",
                code="stale_version",
            )
        if expected_version != int(row.version):
            raise FeatureFlagError(
                "La configuración cambió en otra sesión. Recarga la página.",
                code="stale_version",
            )

        previous = bool(row.habilitada)
        if previous == enabled:
            return FeatureChangeResult(
                changed=False,
                duplicate=False,
                enabled=enabled,
                version=int(row.version),
            )

        if key == TURN_REQUIRED_FEATURE and not enabled:
            active_count = TurnoCaja.objects.filter(
                estado__in=["ABIERTO", "CIERRE"],
            ).count()
            if active_count:
                raise ActiveCashTurnError(active_count)

        actor_name = _actor_name(actor)
        row.habilitada = enabled
        row.version = int(row.version) + 1
        row.actualizada_por = actor
        row.actualizada_por_nombre = actor_name
        row.save(
            update_fields=[
                "habilitada",
                "version",
                "actualizada_en",
                "actualizada_por",
                "actualizada_por_nombre",
            ]
        )
        CambioConfiguracionFuncionalidad.objects.create(
            funcionalidad=row,
            anterior=previous,
            nuevo=enabled,
            motivo=reason[:500],
            cambiado_por=actor,
            cambiado_por_nombre=actor_name,
            ip=ip,
            user_agent=str(user_agent or "")[:300],
            solicitud_id=request_uuid,
        )

    clear_feature_cache(key)
    return FeatureChangeResult(
        changed=True,
        duplicate=False,
        enabled=enabled,
        version=int(row.version),
    )
