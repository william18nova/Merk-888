import json
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import call, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TransactionTestCase

from mainApp.management.commands.reclasificar_productos import (
    Command,
    build_confirmation_token,
    calculate_fingerprint,
    load_active_plaza_destination_ids,
)
from mainApp.models import Categoria, Producto


TEST_CATEGORIES = (
    SimpleNamespace(name="Categoria A", description="Descripcion A"),
    SimpleNamespace(name="Categoria B", description="Descripcion B"),
)


def classification(category, rule="test_rule", confidence="high"):
    return SimpleNamespace(
        category=category,
        rule=rule,
        confidence=confidence,
    )


class PlazaDestinationMappingTests(SimpleTestCase):
    def test_loads_only_unique_active_destination_ids(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mapping.json"
            path.write_text(
                json.dumps(
                    {
                        "mappings": [
                            {"destination_id": 10, "active": True},
                            {"destination_id": "10", "active": True},
                            {"destination_id": 20},
                            {"destination_id": 30, "active": False},
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = load_active_plaza_destination_ids(path)

        self.assertEqual(result, frozenset({10, 20}))

    def test_rejects_an_invalid_active_destination_id(self):
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mapping.json"
            path.write_text(
                json.dumps(
                    {"mappings": [{"destination_id": "", "active": True}]}
                ),
                encoding="utf-8",
            )

            with self.assertRaisesMessage(CommandError, "destination_id invalido"):
                load_active_plaza_destination_ids(path)


class ReclassificationConfirmationTests(SimpleTestCase):
    def test_fingerprint_and_token_are_deterministic(self):
        first = calculate_fingerprint({"products": [2, 1], "version": 1})
        second = calculate_fingerprint({"version": 1, "products": [2, 1]})
        changed = calculate_fingerprint({"version": 1, "products": [1, 2]})

        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertEqual(
            build_confirmation_token(
                first,
                product_count=2,
                change_count=1,
            ),
            f"RECLASIFICAR-2-1-{first[:16].upper()}",
        )


class ReclassificationCommandDatabaseTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.legacy = Categoria.objects.create(
            nombre="Categoria heredada",
            descripcion="No se debe conservar",
        )
        self.canonical_a = Categoria.objects.create(
            nombre="Categoria A",
            descripcion="Descripcion anterior",
        )
        self.product_one = Producto.objects.create(
            nombre="Producto uno prueba reclasificacion",
            descripcion="Primero",
            precio="1000.00",
            categoria=self.legacy,
        )
        self.product_two = Producto.objects.create(
            nombre="Producto dos prueba reclasificacion",
            descripcion="Segundo",
            precio="2000.00",
            categoria=self.canonical_a,
        )

    def _classifier(self, **kwargs):
        target = (
            "Categoria A"
            if kwargs["product_id"] == self.product_one.pk
            else "Categoria B"
        )
        return classification(target)

    @patch(
        "mainApp.management.commands.reclasificar_productos.CATEGORY_DEFINITIONS",
        TEST_CATEGORIES,
    )
    @patch(
        "mainApp.management.commands.reclasificar_productos.load_active_plaza_destination_ids",
        return_value=frozenset({777}),
    )
    def test_default_mode_reports_every_product_without_writing_database(
        self,
        _mapping_loader,
    ):
        output = StringIO()
        with TemporaryDirectory() as report_directory, patch(
            "mainApp.management.commands.reclasificar_productos.classify_product",
            side_effect=self._classifier,
        ) as classifier:
            call_command(
                "reclasificar_productos",
                report_dir=report_directory,
                stdout=output,
            )
            reports = list(Path(report_directory).glob("plan_*.json"))
            report = json.loads(reports[0].read_text(encoding="utf-8"))

        self.product_one.refresh_from_db()
        self.product_two.refresh_from_db()
        self.assertEqual(self.product_one.categoria_id, self.legacy.pk)
        self.assertEqual(self.product_two.categoria_id, self.canonical_a.pk)
        self.assertEqual(len(reports), 1)
        self.assertEqual(report["mode"], "dry_run")
        self.assertEqual(report["product_count"], 2)
        self.assertEqual(report["manual_review_count"], 0)
        self.assertEqual(
            report["category_counts"],
            {"Categoria A": 1, "Categoria B": 1},
        )
        self.assertIn("Fingerprint:", output.getvalue())
        self.assertIn("REVISION MANUAL: 0", output.getvalue())
        self.assertEqual(classifier.call_count, 2)
        classifier.assert_has_calls(
            [
                call(
                    product_id=self.product_one.pk,
                    name=self.product_one.nombre,
                    description="Primero",
                    current_category_name="Categoria heredada",
                    plaza_destination_ids=frozenset({777}),
                ),
                call(
                    product_id=self.product_two.pk,
                    name=self.product_two.nombre,
                    description="Segundo",
                    current_category_name="Categoria A",
                    plaza_destination_ids=frozenset({777}),
                ),
            ]
        )

    @patch(
        "mainApp.management.commands.reclasificar_productos.CATEGORY_DEFINITIONS",
        TEST_CATEGORIES,
    )
    @patch(
        "mainApp.management.commands.reclasificar_productos.load_active_plaza_destination_ids",
        return_value=frozenset(),
    )
    def test_apply_aborts_when_any_product_needs_manual_review(
        self,
        _mapping_loader,
    ):
        def classifier_with_review(**kwargs):
            if kwargs["product_id"] == self.product_one.pk:
                return classification(None, rule="no_match", confidence="review")
            return classification("Categoria B")

        with TemporaryDirectory() as report_directory, patch(
            "mainApp.management.commands.reclasificar_productos.classify_product",
            side_effect=classifier_with_review,
        ):
            with self.assertRaisesMessage(CommandError, "requieren revision manual"):
                call_command(
                    "reclasificar_productos",
                    apply=True,
                    confirm_count=2,
                    confirm_token="cualquier-token",
                    report_dir=report_directory,
                )

        self.product_one.refresh_from_db()
        self.assertEqual(self.product_one.categoria_id, self.legacy.pk)
        self.assertTrue(Categoria.objects.filter(pk=self.legacy.pk).exists())

    @patch(
        "mainApp.management.commands.reclasificar_productos.CATEGORY_DEFINITIONS",
        TEST_CATEGORIES,
    )
    @patch(
        "mainApp.management.commands.reclasificar_productos.load_active_plaza_destination_ids",
        return_value=frozenset(),
    )
    def test_apply_requires_the_exact_count_and_token(
        self,
        _mapping_loader,
    ):
        with TemporaryDirectory() as report_directory, patch(
            "mainApp.management.commands.reclasificar_productos.classify_product",
            side_effect=self._classifier,
        ):
            with self.assertRaisesMessage(CommandError, "--confirm-count"):
                call_command(
                    "reclasificar_productos",
                    apply=True,
                    report_dir=report_directory,
                )

            plan = Command()._make_plan(frozenset())
            with self.assertRaisesMessage(CommandError, "no coincide"):
                call_command(
                    "reclasificar_productos",
                    apply=True,
                    confirm_count=plan.change_count,
                    confirm_token="TOKEN-INCORRECTO",
                    report_dir=report_directory,
                )

        self.assertEqual(Producto.objects.count(), 2)
        self.assertTrue(Categoria.objects.filter(pk=self.legacy.pk).exists())

    @patch(
        "mainApp.management.commands.reclasificar_productos.CATEGORY_DEFINITIONS",
        TEST_CATEGORIES,
    )
    @patch(
        "mainApp.management.commands.reclasificar_productos.load_active_plaza_destination_ids",
        return_value=frozenset({777}),
    )
    @patch.object(Command, "_lock_catalog_tables", return_value=None)
    def test_apply_recalculates_after_lock_backs_up_and_replaces_only_category(
        self,
        _lock_tables,
        _mapping_loader,
    ):
        with TemporaryDirectory() as report_directory, patch(
            "mainApp.management.commands.reclasificar_productos.classify_product",
            side_effect=self._classifier,
        ) as classifier:
            plan = Command()._make_plan(frozenset({777}))
            output = StringIO()
            call_command(
                "reclasificar_productos",
                apply=True,
                confirm_count=plan.change_count,
                confirm_token=plan.confirmation_token,
                report_dir=report_directory,
                stdout=output,
            )
            backup_paths = list(Path(report_directory).glob("backup_*.json"))
            result_paths = list(Path(report_directory).glob("resultado_*.json"))
            backup = json.loads(backup_paths[0].read_text(encoding="utf-8"))
            result = json.loads(result_paths[0].read_text(encoding="utf-8"))

        self.product_one.refresh_from_db()
        self.product_two.refresh_from_db()
        categories = dict(
            Categoria.objects.values_list("nombre", "descripcion")
        )
        self.assertEqual(
            categories,
            {
                "Categoria A": "Descripcion A",
                "Categoria B": "Descripcion B",
            },
        )
        self.assertEqual(self.product_one.categoria.nombre, "Categoria A")
        self.assertEqual(self.product_two.categoria.nombre, "Categoria B")
        self.assertEqual(self.product_one.nombre, "Producto uno prueba reclasificacion")
        self.assertEqual(self.product_one.precio, 1000)
        self.assertFalse(Categoria.objects.filter(pk=self.legacy.pk).exists())
        self.assertEqual(len(backup_paths), 1)
        self.assertEqual(len(result_paths), 1)
        self.assertEqual(backup["mode"], "pre_apply_backup")
        self.assertEqual(len(backup["categories_before"]), 2)
        self.assertEqual(len(backup["products"]), 2)
        self.assertIn("backup_sha256", backup)
        self.assertEqual(result["manual_review_count"], 0)
        self.assertEqual(result["product_count"], 2)
        # Una lectura inicial, otra previa al bloqueo y otra tras adquirirlo.
        self.assertEqual(classifier.call_count, 6)
        self.assertIn("Reclasificacion aplicada y verificada", output.getvalue())

    @patch(
        "mainApp.management.commands.reclasificar_productos.CATEGORY_DEFINITIONS",
        TEST_CATEGORIES,
    )
    @patch(
        "mainApp.management.commands.reclasificar_productos.load_active_plaza_destination_ids",
        return_value=frozenset(),
    )
    @patch.object(Command, "_lock_catalog_tables", return_value=None)
    def test_apply_rejects_a_plan_that_changed_before_the_locked_recalculation(
        self,
        _lock_tables,
        _mapping_loader,
    ):
        with TemporaryDirectory() as report_directory, patch(
            "mainApp.management.commands.reclasificar_productos.classify_product",
            side_effect=self._classifier,
        ):
            original = Command()._make_plan(frozenset())
            changed = replace(
                original,
                fingerprint="f" * 64,
                confirmation_token="RECLASIFICAR-2-2-FFFFFFFFFFFFFFFF",
            )
            with patch.object(
                Command,
                "_make_plan",
                side_effect=[original, changed],
            ), self.assertRaisesMessage(CommandError, "no coincide"):
                call_command(
                    "reclasificar_productos",
                    apply=True,
                    confirm_count=original.change_count,
                    confirm_token=original.confirmation_token,
                    report_dir=report_directory,
                )

        self.product_one.refresh_from_db()
        self.assertEqual(self.product_one.categoria_id, self.legacy.pk)
