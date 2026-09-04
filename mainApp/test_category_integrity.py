import json
from importlib import import_module
from decimal import Decimal

from django.test import RequestFactory, SimpleTestCase, TestCase
from django.db import IntegrityError, transaction
from django.db.models.deletion import PROTECT, ProtectedError

from mainApp.forms import CategoriaForm, EditarCategoriaForm, ProductoForm
from mainApp.models import Categoria, Inventario, Producto, Sucursal
from mainApp.views import GestionInventarioMasivaView


class ProductCategoryMigrationSafetyTests(SimpleTestCase):
    def test_data_update_and_schema_change_use_separate_transactions(self):
        migration = import_module(
            "mainApp.migrations.0033_product_category_integrity"
        ).Migration

        self.assertFalse(migration.atomic)
        self.assertTrue(migration.operations[0].atomic)


class ProductCategoryIntegrityTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.category = Categoria.objects.create(nombre="Categoría válida")
        self.branch = Sucursal.objects.create(nombre="Sucursal de prueba")

    def product_form_data(self, **overrides):
        data = {
            "nombre": "Producto de prueba",
            "descripcion": "",
            "precio": "1000.00",
            "categoria": str(self.category.pk),
            "codigo_de_barras": "",
            "iva": "0",
            "impuesto_consumo": "0",
            "icui": "0",
            "ibua": "0",
        }
        data.update(overrides)
        return data

    def test_product_form_requires_an_existing_category(self):
        missing = ProductoForm(data=self.product_form_data(categoria=""))
        self.assertFalse(missing.is_valid())
        self.assertIn("categoria", missing.errors)

        invalid = ProductoForm(data=self.product_form_data(categoria="999999"))
        self.assertFalse(invalid.is_valid())
        self.assertIn("categoria", invalid.errors)

        valid = ProductoForm(data=self.product_form_data())
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.save().categoria_id, self.category.pk)

    def test_database_requires_and_protects_product_category(self):
        field = Producto._meta.get_field("categoria")
        self.assertFalse(field.null)
        self.assertIs(field.remote_field.on_delete, PROTECT)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Producto.objects.create(nombre="Sin categoría prohibido", precio=1000, iva=0)

        product = Producto.objects.create(
            nombre="Categoría protegida",
            precio=1000,
            iva=0,
            categoria=self.category,
        )
        with self.assertRaises(ProtectedError):
            self.category.delete()
        self.assertTrue(Producto.objects.filter(pk=product.pk).exists())

    def test_uncategorized_name_is_deduplicated_and_cannot_be_renamed(self):
        uncategorized = Categoria.objects.create(nombre="Sin Categoria")
        duplicate = CategoriaForm(data={"nombre": "  Sin categoría  ", "descripcion": ""})
        self.assertFalse(duplicate.is_valid())
        self.assertIn("nombre", duplicate.errors)

        edit = EditarCategoriaForm(
            instance=uncategorized,
            data={"nombre": "Otra categoría", "descripcion": ""},
        )
        self.assertFalse(edit.is_valid())
        self.assertIn("no se puede renombrar", str(edit.errors["nombre"]))

    def test_bulk_create_rejects_missing_category(self):
        request = self.factory.post(
            "/gestion-inventario/",
            data={
                "action": "create_product",
                "sucursal_id": str(self.branch.pk),
                "nombre": "No debe guardarse",
                "precio": "2500",
                "iva": "0",
                "categoria_id": "",
            },
        )

        response = GestionInventarioMasivaView().post(request)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(Producto.objects.filter(nombre="No debe guardarse").exists())

    def test_bulk_update_rejects_empty_category_before_changing_inventory(self):
        product = Producto.objects.create(
            nombre="Producto existente",
            precio=Decimal("1000.00"),
            iva=0,
            categoria=self.category,
        )
        payload = [
            {
                "productId": product.pk,
                "ingresado": 5,
                "producto": {"categoria_id": ""},
            }
        ]
        request = self.factory.post(
            "/gestion-inventario/",
            data={
                "action": "save_rows",
                "sucursal_id": str(self.branch.pk),
                "payload": json.dumps(payload),
            },
        )

        response = GestionInventarioMasivaView().post(request)

        self.assertEqual(response.status_code, 400)
        product.refresh_from_db()
        self.assertEqual(product.categoria_id, self.category.pk)
        self.assertFalse(
            Inventario.objects.filter(
                sucursalid=self.branch,
                productoid=product,
            ).exists()
        )

    def test_bulk_update_validates_every_row_before_writing_anything(self):
        first = Producto.objects.create(
            nombre="Primero lote",
            precio=Decimal("1000.00"),
            iva=0,
            categoria=self.category,
        )
        second = Producto.objects.create(
            nombre="Segundo lote",
            precio=Decimal("2000.00"),
            iva=0,
            categoria=self.category,
        )
        payload = [
            {
                "productId": first.pk,
                "ingresado": 5,
                "producto": {
                    "categoria_id": self.category.pk,
                    "precio": "1500",
                    "iva": "0",
                },
            },
            {
                "productId": second.pk,
                "ingresado": 3,
                "producto": {
                    "categoria_id": self.category.pk,
                    "precio": "precio-invalido",
                    "iva": "0",
                },
            },
        ]
        request = self.factory.post(
            "/gestion-inventario/",
            data={
                "action": "save_rows",
                "sucursal_id": str(self.branch.pk),
                "payload": json.dumps(payload),
            },
        )

        response = GestionInventarioMasivaView().post(request)

        self.assertEqual(response.status_code, 400)
        first.refresh_from_db()
        self.assertEqual(first.precio, Decimal("1000.00"))
        self.assertFalse(Inventario.objects.filter(productoid=first).exists())
