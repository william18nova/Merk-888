import json
import re
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

import psycopg2
from django.conf import settings
from django.db import connection as django_connection
from django.db import transaction

from mainApp.models import Producto


PRICE_QUANTUM = Decimal("0.01")
MAX_PRODUCT_PRICE = Decimal("99999999.99")
SYNC_ADVISORY_LOCK_ID = 2026072901


class ProductPriceSyncError(RuntimeError):
    """La sincronización no puede continuar sin arriesgar datos incorrectos."""


@dataclass(frozen=True)
class PriceMapping:
    source_id: int
    expected_source_name: str
    destination_id: int
    expected_destination_name: str
    equivalence_type: str = "Equivalencia directa"


@dataclass(frozen=True)
class SourceProduct:
    product_id: int
    name: str
    price: Decimal


@dataclass(frozen=True)
class PriceChange:
    source_id: int
    destination_id: int
    destination_name: str
    old_price: Decimal
    new_price: Decimal
    price_factor: Decimal


@dataclass(frozen=True)
class PriceSyncReport:
    applied: bool
    mapping_count: int
    source_product_count: int
    destination_product_count: int
    changes: tuple[PriceChange, ...]
    unchanged_count: int
    suspicious_changes: tuple[PriceChange, ...]


def normalize_product_name(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^0-9a-z]+", " ", text.casefold())
    return " ".join(text.split())


def _positive_integer(value, label):
    if isinstance(value, bool):
        raise ProductPriceSyncError(f"{label} debe ser un entero positivo.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProductPriceSyncError(
            f"{label} debe ser un entero positivo."
        ) from exc
    if parsed <= 0 or str(value).strip() not in {str(parsed), f"{parsed}.0"}:
        raise ProductPriceSyncError(f"{label} debe ser un entero positivo.")
    return parsed


def load_price_mappings(path=None):
    mapping_path = Path(path or settings.PRICE_SYNC_MAPPING_FILE)
    try:
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProductPriceSyncError(
            f"No existe el archivo de mapeo: {mapping_path}."
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductPriceSyncError(
            f"No se pudo leer un mapeo JSON válido en {mapping_path}."
        ) from exc

    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ProductPriceSyncError(
            "El archivo de mapeo debe ser un objeto JSON con version 1."
        )
    raw_mappings = payload.get("mappings")
    if not isinstance(raw_mappings, list) or not raw_mappings:
        raise ProductPriceSyncError("El archivo de mapeo no contiene relaciones.")

    mappings = []
    seen_pairs = set()
    destination_sources = {}
    source_names = {}
    for index, item in enumerate(raw_mappings, start=1):
        if not isinstance(item, dict):
            raise ProductPriceSyncError(
                f"El mapeo #{index} debe ser un objeto JSON."
            )

        source_id = _positive_integer(
            item.get("source_id"),
            f"source_id del mapeo #{index}",
        )
        destination_id = _positive_integer(
            item.get("destination_id"),
            f"destination_id del mapeo #{index}",
        )
        source_name = str(item.get("expected_source_name") or "").strip()
        destination_name = str(
            item.get("expected_destination_name") or ""
        ).strip()
        equivalence_type = str(
            item.get("equivalence_type") or "Equivalencia directa"
        ).strip()
        active = item.get("active", True)
        if not isinstance(active, bool):
            raise ProductPriceSyncError(
                f"active del mapeo #{index} debe ser true o false."
            )
        if not source_name or not destination_name:
            raise ProductPriceSyncError(
                f"El mapeo #{index} necesita los nombres esperados de origen y destino."
            )

        pair = (source_id, destination_id)
        if pair in seen_pairs:
            raise ProductPriceSyncError(
                f"La relación {source_id} -> {destination_id} está repetida."
            )
        seen_pairs.add(pair)

        previous_source = destination_sources.get(destination_id)
        if previous_source is not None and previous_source != source_id:
            raise ProductPriceSyncError(
                f"El producto destino {destination_id} está vinculado con "
                f"dos productos de origen ({previous_source} y {source_id})."
            )
        destination_sources[destination_id] = source_id

        normalized_source_name = normalize_product_name(source_name)
        previous_name = source_names.get(source_id)
        if previous_name is not None and previous_name != normalized_source_name:
            raise ProductPriceSyncError(
                f"El producto de origen {source_id} tiene nombres esperados incompatibles."
            )
        source_names[source_id] = normalized_source_name

        if not active:
            continue
        if equivalence_type != "Equivalencia directa":
            raise ProductPriceSyncError(
                f"El mapeo activo {source_id} -> {destination_id} no es una "
                "equivalencia directa; debe revisarse antes de activarlo."
            )
        mappings.append(
            PriceMapping(
                source_id=source_id,
                expected_source_name=source_name,
                destination_id=destination_id,
                expected_destination_name=destination_name,
                equivalence_type=equivalence_type,
            )
        )
    if not mappings:
        raise ProductPriceSyncError(
            "El archivo de mapeo no contiene relaciones activas."
        )
    return tuple(mappings)


def _validated_price(value, source_id):
    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProductPriceSyncError(
            f"El producto externo {source_id} tiene un precio inválido."
        ) from exc
    if not price.is_finite() or price <= 0 or price > MAX_PRODUCT_PRICE:
        raise ProductPriceSyncError(
            f"El producto externo {source_id} tiene un precio fuera del rango permitido."
        )
    try:
        rounded = price.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ProductPriceSyncError(
            f"El producto externo {source_id} tiene un precio inválido."
        ) from exc
    if rounded <= 0:
        raise ProductPriceSyncError(
            f"El producto externo {source_id} tiene un precio menor a un centavo."
        )
    return rounded


def _validate_source_rows(rows, mappings):
    products = {}
    for row in rows:
        if len(row) != 3:
            raise ProductPriceSyncError(
                "La consulta externa devolvió una estructura inesperada."
            )
        product_id = _positive_integer(row[0], "productoid externo")
        if product_id in products:
            raise ProductPriceSyncError(
                f"La base externa devolvió dos filas para el ID {product_id}."
            )
        products[product_id] = SourceProduct(
            product_id=product_id,
            name=str(row[1] or "").strip(),
            price=_validated_price(row[2], product_id),
        )

    expected_ids = {mapping.source_id for mapping in mappings}
    missing = sorted(expected_ids - products.keys())
    unexpected = sorted(products.keys() - expected_ids)
    if missing or unexpected:
        fragments = []
        if missing:
            fragments.append(
                "faltan IDs " + ", ".join(map(str, missing[:12]))
            )
        if unexpected:
            fragments.append(
                "sobran IDs " + ", ".join(map(str, unexpected[:12]))
            )
        raise ProductPriceSyncError(
            "La lectura de productos externos está incompleta: "
            + "; ".join(fragments)
            + "."
        )

    expected_names = {}
    for mapping in mappings:
        expected_names.setdefault(
            mapping.source_id,
            mapping.expected_source_name,
        )

    mismatches = []
    for source_id, expected_name in expected_names.items():
        actual_name = products[source_id].name
        if normalize_product_name(actual_name) != normalize_product_name(expected_name):
            mismatches.append((source_id, expected_name, actual_name))
    if mismatches:
        examples = "; ".join(
            f"{source_id}: se esperaba '{expected}', se encontró '{actual}'"
            for source_id, expected, actual in mismatches[:6]
        )
        suffix = (
            f" Hay {len(mismatches)} inconsistencias en total."
            if len(mismatches) > 6
            else ""
        )
        raise ProductPriceSyncError(
            "Los IDs externos no corresponden al catálogo del mapeo. "
            + examples
            + "."
            + suffix
        )
    return products


def _source_connection_options():
    required = {
        "PRICE_SYNC_SOURCE_HOST": settings.PRICE_SYNC_SOURCE_HOST,
        "PRICE_SYNC_SOURCE_NAME": settings.PRICE_SYNC_SOURCE_NAME,
        "PRICE_SYNC_SOURCE_USER": settings.PRICE_SYNC_SOURCE_USER,
        "PRICE_SYNC_SOURCE_PASSWORD": settings.PRICE_SYNC_SOURCE_PASSWORD,
    }
    missing = [name for name, value in required.items() if not str(value).strip()]
    if missing:
        raise ProductPriceSyncError(
            "Faltan variables de conexión para la base externa: "
            + ", ".join(missing)
            + "."
        )

    sslmode = str(settings.PRICE_SYNC_SOURCE_SSLMODE).strip().lower()
    if sslmode not in {"require", "verify-ca", "verify-full"}:
        raise ProductPriceSyncError(
            "PRICE_SYNC_SOURCE_SSLMODE debe exigir una conexión TLS."
        )
    sslrootcert = str(settings.PRICE_SYNC_SOURCE_SSLROOTCERT or "").strip()
    if sslmode in {"verify-ca", "verify-full"} and not sslrootcert:
        raise ProductPriceSyncError(
            "PRICE_SYNC_SOURCE_SSLROOTCERT es obligatorio para verificar "
            "el certificado de la base externa."
        )
    try:
        port = int(settings.PRICE_SYNC_SOURCE_PORT)
        timeout = int(settings.PRICE_SYNC_CONNECT_TIMEOUT)
    except (TypeError, ValueError) as exc:
        raise ProductPriceSyncError(
            "El puerto y el tiempo de conexión externos deben ser enteros."
        ) from exc

    options = {
        "host": settings.PRICE_SYNC_SOURCE_HOST,
        "port": port,
        "dbname": settings.PRICE_SYNC_SOURCE_NAME,
        "user": settings.PRICE_SYNC_SOURCE_USER,
        "password": settings.PRICE_SYNC_SOURCE_PASSWORD,
        "sslmode": sslmode,
        "connect_timeout": timeout,
        "application_name": "merk2_price_sync",
    }
    if sslrootcert:
        options["sslrootcert"] = sslrootcert
    return options


def _rollback_safely(connection):
    if connection is None:
        return
    try:
        connection.rollback()
    except Exception:
        # La conexión puede haberse roto; nunca se debe ocultar el error original.
        pass


def fetch_source_products(mappings, connect=None):
    source_ids = sorted({mapping.source_id for mapping in mappings})
    connect = connect or psycopg2.connect
    connection = None
    try:
        connection = connect(**_source_connection_options())
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            read_only = str(cursor.fetchone()[0]).strip().lower()
            if read_only != "on":
                raise ProductPriceSyncError(
                    "La sesión externa no quedó en modo de solo lectura."
                )
            cursor.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                [f"{int(settings.PRICE_SYNC_STATEMENT_TIMEOUT_MS)}ms"],
            )
            cursor.execute(
                """
                SELECT productoid, nombre, precio
                  FROM public.productos
                 WHERE productoid = ANY(%s)
                 ORDER BY productoid
                """,
                (source_ids,),
            )
            rows = cursor.fetchall()
        _rollback_safely(connection)
    except ProductPriceSyncError:
        _rollback_safely(connection)
        raise
    except Exception as exc:
        _rollback_safely(connection)
        raise ProductPriceSyncError(
            "No se pudieron consultar los precios en la base externa."
        ) from exc
    finally:
        if connection is not None:
            connection.close()

    return _validate_source_rows(rows, mappings)


def _load_destination_products(mappings, *, lock):
    destination_ids = sorted(
        {mapping.destination_id for mapping in mappings}
    )
    queryset = Producto.objects
    if lock:
        queryset = queryset.select_for_update()
    products = queryset.filter(productoid__in=destination_ids).in_bulk()

    missing = sorted(set(destination_ids) - products.keys())
    if missing:
        raise ProductPriceSyncError(
            "Faltan productos en merk2: "
            + ", ".join(map(str, missing[:12]))
            + "."
        )

    mismatches = []
    for mapping in mappings:
        actual_name = products[mapping.destination_id].nombre
        if normalize_product_name(actual_name) != normalize_product_name(
            mapping.expected_destination_name
        ):
            mismatches.append(
                (
                    mapping.destination_id,
                    mapping.expected_destination_name,
                    actual_name,
                )
            )
    if mismatches:
        examples = "; ".join(
            f"{product_id}: se esperaba '{expected}', se encontró '{actual}'"
            for product_id, expected, actual in mismatches[:6]
        )
        raise ProductPriceSyncError(
            "Los IDs de merk2 no corresponden al catálogo del mapeo. "
            + examples
            + "."
        )
    return products


def _price_factor(old_price, new_price):
    old_price = Decimal(old_price)
    if old_price <= 0:
        return Decimal("Infinity")
    return max(new_price / old_price, old_price / new_price).quantize(
        Decimal("0.0001"),
        rounding=ROUND_HALF_UP,
    )


@contextmanager
def _exclusive_sync_lock():
    try:
        with django_connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_lock(%s)",
                [SYNC_ADVISORY_LOCK_ID],
            )
            acquired = bool(cursor.fetchone()[0])
    except Exception as exc:
        raise ProductPriceSyncError(
            "No se pudo adquirir el bloqueo exclusivo de sincronización."
        ) from exc

    if not acquired:
        raise ProductPriceSyncError(
            "Ya hay otra sincronización de precios en ejecución."
        )
    try:
        yield
    finally:
        try:
            with django_connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_unlock(%s)",
                    [SYNC_ADVISORY_LOCK_ID],
                )
        except Exception:
            # Cerrar la sesión también libera el advisory lock.
            django_connection.close()


def _build_report(mappings, source_products, destination_products, *, applied):
    try:
        maximum_factor = Decimal(str(settings.PRICE_SYNC_MAX_PRICE_FACTOR))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ProductPriceSyncError(
            "PRICE_SYNC_MAX_PRICE_FACTOR debe ser un número válido."
        ) from exc
    if not maximum_factor.is_finite() or maximum_factor < 1:
        raise ProductPriceSyncError(
            "PRICE_SYNC_MAX_PRICE_FACTOR debe ser mayor o igual a 1."
        )

    changes = []
    suspicious = []
    unchanged_count = 0
    for mapping in mappings:
        product = destination_products[mapping.destination_id]
        old_price = Decimal(product.precio).quantize(PRICE_QUANTUM)
        new_price = source_products[mapping.source_id].price
        if old_price == new_price:
            unchanged_count += 1
            continue
        change = PriceChange(
            source_id=mapping.source_id,
            destination_id=mapping.destination_id,
            destination_name=product.nombre,
            old_price=old_price,
            new_price=new_price,
            price_factor=_price_factor(old_price, new_price),
        )
        changes.append(change)
        if change.price_factor > maximum_factor:
            suspicious.append(change)

    return PriceSyncReport(
        applied=applied,
        mapping_count=len(mappings),
        source_product_count=len(source_products),
        destination_product_count=len(destination_products),
        changes=tuple(changes),
        unchanged_count=unchanged_count,
        suspicious_changes=tuple(suspicious),
    )


def sync_product_prices(*, apply=False, mappings=None, connect=None):
    mappings = tuple(
        load_price_mappings() if mappings is None else mappings
    )
    if not mappings:
        raise ProductPriceSyncError("No hay productos configurados para sincronizar.")

    if not apply:
        source_products = fetch_source_products(mappings, connect=connect)
        destination_products = _load_destination_products(mappings, lock=False)
        return _build_report(
            mappings,
            source_products,
            destination_products,
            applied=False,
        )

    with _exclusive_sync_lock():
        source_products = fetch_source_products(mappings, connect=connect)
        with transaction.atomic():
            destination_products = _load_destination_products(mappings, lock=True)
            report = _build_report(
                mappings,
                source_products,
                destination_products,
                applied=True,
            )
            if report.suspicious_changes:
                ids = ", ".join(
                    str(change.destination_id)
                    for change in report.suspicious_changes[:12]
                )
                raise ProductPriceSyncError(
                    "Se bloquearon cambios de precio con una variación superior al "
                    f"factor permitido: {ids}. Ejecuta primero el modo de prueba."
                )

            changed_products = []
            for change in report.changes:
                product = destination_products[change.destination_id]
                product.precio_anterior = change.old_price
                product.precio = change.new_price
                changed_products.append(product)
            if changed_products:
                Producto.objects.bulk_update(
                    changed_products,
                    ["precio_anterior", "precio"],
                    batch_size=100,
                )
            return report
