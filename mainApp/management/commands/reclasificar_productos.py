"""Reclasifica de forma segura todo el catalogo de productos.

El comando es deliberadamente conservador: una ejecucion normal solo genera
un informe. Para escribir en la base de datos se necesita repetir exactamente
el conteo y el token producidos por la simulacion, y no puede quedar ningun
producto marcado para revision manual.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from typing import Iterable, Sequence

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from mainApp.models import Categoria, Producto
from mainApp.services.product_categorization import (
    CATEGORY_DEFINITIONS,
    classify_product,
)


REPORT_SCHEMA_VERSION = 1
PLAZA_MAPPING_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "price_sync_plaza_map.json"
)
DEFAULT_REPORT_DIR = (
    Path(settings.BASE_DIR) / "outputs" / "product_category_reclassification"
)


@dataclass(frozen=True)
class CategorySnapshot:
    category_id: int
    name: str
    description: str | None


@dataclass
class ProductDecision:
    product: Producto
    product_id: int
    name: str
    description: str
    old_category_id: int | None
    old_category_name: str
    new_category: str | None
    selected_category_id: int | None
    rule: str
    confidence: str
    manual_review: bool
    will_change: bool


@dataclass
class ReclassificationPlan:
    categories: tuple[CategorySnapshot, ...]
    decisions: tuple[ProductDecision, ...]
    plaza_destination_ids: frozenset[int]
    fingerprint: str
    confirmation_token: str

    @property
    def product_count(self) -> int:
        return len(self.decisions)

    @property
    def change_count(self) -> int:
        return sum(decision.will_change for decision in self.decisions)

    @property
    def unchanged_count(self) -> int:
        return self.product_count - self.change_count

    @property
    def manual_review_count(self) -> int:
        return sum(decision.manual_review for decision in self.decisions)


def _category_key(value: str | None) -> str:
    """Clave tolerante para reutilizar nombres con/sin tildes."""

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return " ".join(text.split()).casefold()


def _canonical_catalog() -> tuple[tuple[str, str], ...]:
    catalog: list[tuple[str, str]] = []
    seen: set[str] = set()
    for definition in CATEGORY_DEFINITIONS:
        name = str(definition.name).strip()
        description = str(definition.description or "").strip()
        key = _category_key(name)
        if not name or not key:
            raise CommandError("La taxonomia contiene una categoria sin nombre.")
        if key in seen:
            raise CommandError(
                f"La taxonomia contiene la categoria duplicada: {name}."
            )
        seen.add(key)
        catalog.append((name, description))
    if not catalog:
        raise CommandError("La taxonomia canonica esta vacia.")
    return tuple(catalog)


def load_active_plaza_destination_ids(
    mapping_path: Path = PLAZA_MAPPING_PATH,
) -> frozenset[int]:
    """Lee unicamente los IDs destino de los mapeos activos de Plaza."""

    try:
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CommandError(
            f"No se pudo leer el mapeo de Plaza {mapping_path}: {exc}"
        ) from exc

    mappings = payload.get("mappings") if isinstance(payload, dict) else None
    if not isinstance(mappings, list):
        raise CommandError(
            f"El mapeo de Plaza {mapping_path} no contiene una lista 'mappings'."
        )

    destination_ids: set[int] = set()
    for position, mapping in enumerate(mappings, start=1):
        if not isinstance(mapping, dict):
            raise CommandError(
                f"Mapeo de Plaza invalido en la posicion {position}."
            )
        if mapping.get("active", True) is not True:
            continue
        raw_destination_id = mapping.get("destination_id")
        if isinstance(raw_destination_id, bool):
            raise CommandError(
                f"destination_id invalido en el mapeo activo {position}."
            )
        try:
            destination_id = int(raw_destination_id)
        except (TypeError, ValueError) as exc:
            raise CommandError(
                f"destination_id invalido en el mapeo activo {position}."
            ) from exc
        if destination_id <= 0:
            raise CommandError(
                f"destination_id invalido en el mapeo activo {position}."
            )
        destination_ids.add(destination_id)

    return frozenset(destination_ids)


def _select_existing_canonical_ids(
    categories: Sequence[CategorySnapshot],
    canonical_catalog: Sequence[tuple[str, str]],
) -> dict[str, int | None]:
    """Elige una sola fila existente para cada categoria canonica."""

    result: dict[str, int | None] = {}
    for canonical_name, _description in canonical_catalog:
        candidates = [
            category
            for category in categories
            if _category_key(category.name) == _category_key(canonical_name)
        ]
        # Se prefiere el nombre ya exacto y luego el PK menor. Las filas
        # duplicadas quedaran vacias y se eliminaran al final de --apply.
        candidates.sort(
            key=lambda category: (
                category.name != canonical_name,
                category.category_id,
            )
        )
        result[canonical_name] = (
            candidates[0].category_id if candidates else None
        )
    return result


def _category_mutation_counts(plan: ReclassificationPlan) -> dict[str, int]:
    """Resume las mutaciones de categorías incluidas en el plan."""

    catalog = _canonical_catalog()
    selected = _select_existing_canonical_ids(plan.categories, catalog)
    by_id = {category.category_id: category for category in plan.categories}
    selected_ids = {category_id for category_id in selected.values() if category_id}
    created = sum(category_id is None for category_id in selected.values())
    updated = 0
    for name, description in catalog:
        category_id = selected[name]
        if category_id is None:
            continue
        category = by_id[category_id]
        if category.name != name or (category.description or "") != description:
            updated += 1
    deleted = sum(
        category.category_id not in selected_ids for category in plan.categories
    )
    return {"create": created, "update": updated, "delete": deleted}


def _decision_payload(decision: ProductDecision) -> dict:
    return {
        "product_id": decision.product_id,
        "name": decision.name,
        "description": decision.description,
        "old_category_id": decision.old_category_id,
        "old_category_name": decision.old_category_name,
        "new_category": decision.new_category,
        "selected_category_id": decision.selected_category_id,
        "rule": decision.rule,
        "confidence": decision.confidence,
        "manual_review": decision.manual_review,
        "will_change": decision.will_change,
    }


def _fingerprint_payload(
    *,
    categories: Sequence[CategorySnapshot],
    decisions: Sequence[ProductDecision],
    plaza_destination_ids: Iterable[int],
    canonical_catalog: Sequence[tuple[str, str]],
) -> dict:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "canonical_categories": [
            {"name": name, "description": description}
            for name, description in canonical_catalog
        ],
        "existing_categories": [
            {
                "category_id": category.category_id,
                "name": category.name,
                "description": category.description,
            }
            for category in categories
        ],
        "plaza_destination_ids": sorted(plaza_destination_ids),
        "products": [_decision_payload(decision) for decision in decisions],
    }


def calculate_fingerprint(payload: dict) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def build_confirmation_token(
    fingerprint: str,
    *,
    product_count: int,
    change_count: int,
) -> str:
    return (
        f"RECLASIFICAR-{product_count}-{change_count}-"
        f"{fingerprint[:16].upper()}"
    )


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Escribe JSON completo y lo publica con un reemplazo atomico."""

    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        # En POSIX, fsync del directorio hace durable también el rename. Windows
        # no permite abrir directorios de esta forma y ya se sincronizó el archivo.
        if os.name != "nt":
            directory_fd = os.open(
                path.parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _timestamp() -> str:
    return datetime.now(datetime_timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class Command(BaseCommand):
    help = (
        "Reclasifica todos los productos con la taxonomia canonica. "
        "Sin --apply solo genera una simulacion verificable."
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Aplica el plan confirmado dentro de una transaccion.",
        )
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Ejecuta explicitamente la simulacion (modo predeterminado).",
        )
        parser.add_argument(
            "--confirm-count",
            type=int,
            default=None,
            help="Cantidad exacta de productos a cambiar mostrada por el dry-run.",
        )
        parser.add_argument(
            "--confirm-token",
            default="",
            help="Token exacto generado por el dry-run.",
        )
        parser.add_argument(
            "--report-dir",
            default=str(DEFAULT_REPORT_DIR),
            help="Directorio para el informe y el respaldo JSON.",
        )

    def _resolve_report_dir(self, raw_path: str) -> Path:
        path = Path(raw_path).expanduser()
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path
        return path.resolve()

    def _read_categories(self) -> tuple[CategorySnapshot, ...]:
        return tuple(
            CategorySnapshot(
                category_id=category_id,
                name=name or "",
                description=description,
            )
            for category_id, name, description in Categoria.objects.order_by(
                "categoriaid"
            ).values_list("categoriaid", "nombre", "descripcion")
        )

    def _make_plan(
        self,
        plaza_destination_ids: frozenset[int],
    ) -> ReclassificationPlan:
        canonical_catalog = _canonical_catalog()
        canonical_name_by_key = {
            _category_key(name): name for name, _description in canonical_catalog
        }
        categories = self._read_categories()
        selected_ids = _select_existing_canonical_ids(
            categories,
            canonical_catalog,
        )

        # ``prefetch_related`` conserva también filas legacy cuyo FK sigue en
        # NULL antes de aplicar la migración de integridad. ``select_related``
        # usaría INNER JOIN porque el modelo nuevo ya declara null=False y
        # ocultaría esas filas durante el despliegue escalonado.
        products = tuple(
            Producto.objects.prefetch_related("categoria").order_by("productoid")
        )
        decisions: list[ProductDecision] = []
        for product in products:
            old_category_id = product.categoria_id
            old_category = product.categoria if old_category_id is not None else None
            old_category_name = old_category.nombre if old_category else ""
            try:
                classification = classify_product(
                    product_id=product.productoid,
                    name=product.nombre or "",
                    description=product.descripcion or "",
                    current_category_name=old_category_name,
                    plaza_destination_ids=plaza_destination_ids,
                )
            except Exception as exc:
                raise CommandError(
                    "Fallo la clasificacion del producto "
                    f"{product.productoid} ({product.nombre}): {exc}"
                ) from exc

            raw_category = classification.category
            canonical_name = (
                canonical_name_by_key.get(_category_key(raw_category))
                if raw_category
                else None
            )
            invalid_category = bool(raw_category) and canonical_name is None
            confidence = str(classification.confidence or "").strip().lower()
            rule = str(classification.rule or "").strip()
            valid_confidences = {"high", "medium", "low", "review"}
            invalid_confidence = confidence not in valid_confidences
            unsafe_low_confidence = confidence == "low" and not (
                canonical_name == "Sin categoría"
                and rule.startswith("fallback:")
            )
            manual_review = (
                canonical_name is None
                or invalid_category
                or confidence == "review"
                or invalid_confidence
                or unsafe_low_confidence
            )
            selected_category_id = (
                selected_ids[canonical_name] if canonical_name else None
            )
            will_change = manual_review or (
                selected_category_id is None
                or old_category_id != selected_category_id
            )
            if invalid_category:
                rule = f"invalid_category:{raw_category};{rule}"
            if invalid_confidence:
                rule = f"invalid_confidence:{confidence or '<empty>'};{rule}"
            elif unsafe_low_confidence:
                rule = f"unsafe_low_confidence:{rule}"

            decisions.append(
                ProductDecision(
                    product=product,
                    product_id=product.productoid,
                    name=product.nombre or "",
                    description=product.descripcion or "",
                    old_category_id=old_category_id,
                    old_category_name=old_category_name,
                    new_category=canonical_name,
                    selected_category_id=selected_category_id,
                    rule=rule,
                    confidence=confidence,
                    manual_review=manual_review,
                    will_change=will_change,
                )
            )

        decisions_tuple = tuple(decisions)
        fingerprint_data = _fingerprint_payload(
            categories=categories,
            decisions=decisions_tuple,
            plaza_destination_ids=plaza_destination_ids,
            canonical_catalog=canonical_catalog,
        )
        fingerprint = calculate_fingerprint(fingerprint_data)
        change_count = sum(decision.will_change for decision in decisions_tuple)
        confirmation_token = build_confirmation_token(
            fingerprint,
            product_count=len(decisions_tuple),
            change_count=change_count,
        )
        return ReclassificationPlan(
            categories=categories,
            decisions=decisions_tuple,
            plaza_destination_ids=plaza_destination_ids,
            fingerprint=fingerprint,
            confirmation_token=confirmation_token,
        )

    def _report_payload(self, plan: ReclassificationPlan, *, mode: str) -> dict:
        counts = Counter(
            decision.new_category
            for decision in plan.decisions
            if not decision.manual_review and decision.new_category
        )
        category_mutations = _category_mutation_counts(plan)
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "mode": mode,
            "generated_at_utc": datetime.now(
                datetime_timezone.utc
            ).isoformat(),
            "product_count": plan.product_count,
            "change_count": plan.change_count,
            "unchanged_count": plan.unchanged_count,
            "manual_review_count": plan.manual_review_count,
            "category_mutations": category_mutations,
            "category_counts": {
                name: counts.get(name, 0)
                for name, _description in _canonical_catalog()
            },
            "fingerprint": plan.fingerprint,
            "confirmation_token": plan.confirmation_token,
            "plaza_destination_ids": sorted(plan.plaza_destination_ids),
            "categories_before": [
                {
                    "category_id": category.category_id,
                    "name": category.name,
                    "description": category.description,
                }
                for category in plan.categories
            ],
            "products": [
                _decision_payload(decision) for decision in plan.decisions
            ],
        }

    def _write_plan_report(
        self,
        plan: ReclassificationPlan,
        report_dir: Path,
        *,
        mode: str,
    ) -> Path:
        path = report_dir / (
            f"plan_reclasificacion_{_timestamp()}_{plan.fingerprint[:12]}.json"
        )
        try:
            _atomic_write_json(path, self._report_payload(plan, mode=mode))
        except OSError as exc:
            raise CommandError(
                f"No se pudo escribir el informe de reclasificacion: {exc}"
            ) from exc
        return path

    def _print_summary(self, plan: ReclassificationPlan) -> None:
        counts = Counter(
            decision.new_category
            for decision in plan.decisions
            if not decision.manual_review and decision.new_category
        )
        self.stdout.write(
            "PLAN: "
            f"{plan.product_count} productos, "
            f"{plan.change_count} asignaciones por cambiar, "
            f"{plan.unchanged_count} sin cambio y "
            f"{plan.manual_review_count} para revision manual."
        )
        category_mutations = _category_mutation_counts(plan)
        self.stdout.write(
            "Categorías: "
            f"{category_mutations['create']} por crear, "
            f"{category_mutations['update']} por actualizar y "
            f"{category_mutations['delete']} antiguas por eliminar."
        )
        self.stdout.write("Resumen por categoria:")
        for name, _description in _canonical_catalog():
            self.stdout.write(f"- {name}: {counts.get(name, 0)}")
        self.stdout.write(
            f"- REVISION MANUAL: {plan.manual_review_count}"
        )
        self.stdout.write(f"Fingerprint: {plan.fingerprint}")
        self.stdout.write(
            f"Conteo de confirmacion: {plan.change_count}"
        )
        self.stdout.write(
            f"Token de confirmacion: {plan.confirmation_token}"
        )

    def _validate_apply_confirmation(
        self,
        plan: ReclassificationPlan,
        *,
        confirm_count: int | None,
        confirm_token: str,
    ) -> None:
        if plan.manual_review_count:
            raise CommandError(
                "No se puede aplicar: "
                f"{plan.manual_review_count} productos requieren revision manual."
            )
        if confirm_count is None or not confirm_token:
            raise CommandError(
                "--apply exige --confirm-count y --confirm-token tomados "
                "de la simulacion mas reciente."
            )
        if confirm_count != plan.change_count:
            raise CommandError(
                "El --confirm-count no coincide con el plan actual: "
                f"se esperaba {plan.change_count}."
            )
        if confirm_token != plan.confirmation_token:
            raise CommandError(
                "El --confirm-token no coincide con el fingerprint actual. "
                "Ejecuta de nuevo la simulacion."
            )

    def _lock_catalog_tables(self) -> None:
        if connection.vendor != "postgresql":
            raise CommandError(
                "--apply solo está habilitado con PostgreSQL porque requiere "
                "bloqueos de tabla para proteger el catálogo completo."
            )
        quote = connection.ops.quote_name
        category_table = quote(Categoria._meta.db_table)
        product_table = quote(Producto._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '20s'")
            cursor.execute(
                "LOCK TABLE "
                f"{category_table}, {product_table} "
                "IN SHARE ROW EXCLUSIVE MODE"
            )

    def _write_backup(
        self,
        plan: ReclassificationPlan,
        report_dir: Path,
    ) -> Path:
        path = report_dir / (
            f"backup_categorias_{_timestamp()}_{plan.fingerprint[:12]}.json"
        )
        payload = self._report_payload(plan, mode="pre_apply_backup")
        payload["backup_sha256"] = calculate_fingerprint(payload)
        try:
            _atomic_write_json(path, payload)
            verified = json.loads(path.read_text(encoding="utf-8"))
            stored_hash = verified.pop("backup_sha256", None)
            if not stored_hash or calculate_fingerprint(verified) != stored_hash:
                raise OSError("la verificación SHA-256 del respaldo falló")
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CommandError(
                "No se pudo crear el respaldo obligatorio; no se modifico "
                f"la base de datos: {exc}"
            ) from exc
        return path

    def _ensure_canonical_categories(self) -> dict[str, Categoria]:
        canonical_catalog = _canonical_catalog()
        existing = list(Categoria.objects.order_by("categoriaid"))
        snapshots = tuple(
            CategorySnapshot(
                category_id=category.categoriaid,
                name=category.nombre or "",
                description=category.descripcion,
            )
            for category in existing
        )
        selected_ids = _select_existing_canonical_ids(
            snapshots,
            canonical_catalog,
        )
        by_id = {category.categoriaid: category for category in existing}
        canonical_objects: dict[str, Categoria] = {}
        for name, description in canonical_catalog:
            selected_id = selected_ids[name]
            if selected_id is None:
                category = Categoria.objects.create(
                    nombre=name,
                    descripcion=description,
                )
            else:
                category = by_id[selected_id]
                update_fields: list[str] = []
                if category.nombre != name:
                    category.nombre = name
                    update_fields.append("nombre")
                if (category.descripcion or "") != description:
                    category.descripcion = description
                    update_fields.append("descripcion")
                if update_fields:
                    category.save(update_fields=update_fields)
            canonical_objects[name] = category
        return canonical_objects

    def _verify_product_assignments(
        self,
        plan: ReclassificationPlan,
        canonical_objects: dict[str, Categoria],
    ) -> None:
        expected = {
            decision.product_id: canonical_objects[decision.new_category].pk
            for decision in plan.decisions
            if decision.new_category is not None
            and not decision.manual_review
        }
        actual = dict(
            Producto.objects.order_by("productoid").values_list(
                "productoid", "categoria_id"
            )
        )
        if len(actual) != plan.product_count:
            raise CommandError(
                "La cantidad de productos cambio durante la reclasificacion; "
                "se revirtio la transaccion."
            )
        if Producto.objects.filter(categoria__isnull=True).exists():
            raise CommandError(
                "La verificacion encontro productos sin categoria; "
                "se revirtio la transaccion."
            )
        mismatches = [
            product_id
            for product_id, expected_category_id in expected.items()
            if actual.get(product_id) != expected_category_id
        ]
        if mismatches:
            sample = ", ".join(str(value) for value in mismatches[:10])
            raise CommandError(
                "La categoria final no coincide con el plan para los productos "
                f"{sample}; se revirtio la transaccion."
            )

    def _delete_empty_old_categories(
        self,
        plan: ReclassificationPlan,
        canonical_objects: dict[str, Categoria],
    ) -> int:
        canonical_ids = {category.pk for category in canonical_objects.values()}
        old_ids = {category.category_id for category in plan.categories}
        deleted = 0
        for category_id in sorted(old_ids - canonical_ids):
            if Producto.objects.filter(categoria_id=category_id).exists():
                raise CommandError(
                    "La categoria antigua "
                    f"{category_id} aun contiene productos; se revirtio "
                    "la transaccion."
                )
            category = Categoria.objects.filter(pk=category_id).first()
            if category is not None:
                category.delete()
                deleted += 1
        return deleted

    def _verify_final_catalog(self) -> None:
        expected = list(_canonical_catalog())
        actual = list(
            Categoria.objects.order_by("nombre", "categoriaid").values_list(
                "nombre", "descripcion"
            )
        )
        expected_normalized = sorted(
            (name, description or "") for name, description in expected
        )
        actual_normalized = sorted(
            (name, description or "") for name, description in actual
        )
        if actual_normalized != expected_normalized:
            raise CommandError(
                "El catalogo final de categorias no coincide exactamente con "
                "la taxonomia canonica; se revirtio la transaccion."
            )

    def _apply_locked_plan(
        self,
        plan: ReclassificationPlan,
        report_dir: Path,
    ) -> tuple[Path, int, int]:
        backup_path = self._write_backup(plan, report_dir)
        canonical_objects = self._ensure_canonical_categories()

        changed_products: list[Producto] = []
        for decision in plan.decisions:
            if decision.manual_review or decision.new_category is None:
                raise CommandError(
                    "El plan contiene revision manual; se revirtio la transaccion."
                )
            product = decision.product
            target_category = canonical_objects[decision.new_category]
            if product.categoria_id != target_category.pk:
                product.categoria = target_category
                changed_products.append(product)

        if changed_products:
            Producto.objects.bulk_update(
                changed_products,
                ["categoria"],
                batch_size=500,
            )

        self._verify_product_assignments(plan, canonical_objects)
        deleted_categories = self._delete_empty_old_categories(
            plan,
            canonical_objects,
        )
        self._verify_product_assignments(plan, canonical_objects)
        self._verify_final_catalog()
        return backup_path, len(changed_products), deleted_categories

    def handle(self, *args, **options):
        report_dir = self._resolve_report_dir(options["report_dir"])
        plaza_destination_ids = load_active_plaza_destination_ids()
        plan = self._make_plan(plaza_destination_ids)
        self._print_summary(plan)
        plan_report_path = self._write_plan_report(
            plan,
            report_dir,
            mode="apply_preflight" if options["apply"] else "dry_run",
        )
        self.stdout.write(f"Informe: {plan_report_path}")

        if not options["apply"]:
            if plan.manual_review_count:
                self.stdout.write(
                    self.style.WARNING(
                        "No se puede aplicar todavia: resuelve todos los "
                        "productos de REVISION MANUAL y repite la simulacion."
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "Simulacion terminada; no se modifico la base de datos."
                    )
                )
            return

        self._validate_apply_confirmation(
            plan,
            confirm_count=options["confirm_count"],
            confirm_token=options["confirm_token"],
        )

        try:
            with transaction.atomic(durable=True):
                self._lock_catalog_tables()
                locked_plaza_destination_ids = load_active_plaza_destination_ids()
                locked_plan = self._make_plan(locked_plaza_destination_ids)
                self._validate_apply_confirmation(
                    locked_plan,
                    confirm_count=options["confirm_count"],
                    confirm_token=options["confirm_token"],
                )
                backup_path, updated_count, deleted_categories = (
                    self._apply_locked_plan(locked_plan, report_dir)
                )
        except CommandError:
            raise
        except (DatabaseError, OSError) as exc:
            raise CommandError(
                "No se pudo aplicar la reclasificacion; la transaccion fue "
                f"revertida: {exc}"
            ) from exc

        result_payload = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "mode": "applied",
            "applied_at_utc": datetime.now(
                datetime_timezone.utc
            ).isoformat(),
            "product_count": locked_plan.product_count,
            "updated_product_count": updated_count,
            "deleted_old_category_count": deleted_categories,
            "manual_review_count": locked_plan.manual_review_count,
            "fingerprint": locked_plan.fingerprint,
            "confirmation_token": locked_plan.confirmation_token,
            "backup_path": str(backup_path),
        }
        result_path = report_dir / (
            f"resultado_reclasificacion_{_timestamp()}_"
            f"{locked_plan.fingerprint[:12]}.json"
        )
        try:
            _atomic_write_json(result_path, result_payload)
        except OSError as exc:
            self.stderr.write(
                self.style.WARNING(
                    "La reclasificacion se aplico, pero no se pudo escribir "
                    f"el informe final: {exc}"
                )
            )
        else:
            self.stdout.write(f"Resultado: {result_path}")

        self.stdout.write(f"Respaldo: {backup_path}")
        self.stdout.write(
            self.style.SUCCESS(
                "Reclasificacion aplicada y verificada: "
                f"{updated_count} productos actualizados y "
                f"{deleted_categories} categorias antiguas vacias eliminadas."
            )
        )
