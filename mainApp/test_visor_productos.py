import json
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from .models import Categoria, Producto, Usuario, Rol, Sucursal, PuntosPago, TurnoCaja, Inventario, Venta
from .permissions import user_can_access_url_name, _resolve_nav_item
from .views import (ProductoBuscarVisorCajeroView, VisorProductosCajeroView,
                    ProductoBuscarBarrasVisorView, VisorProductoBarcodeView)


class VisorProductosTests(TestCase):
    def setUp(self):
        category = Categoria.objects.create(nombre="Frutas")
        self.product = Producto.objects.create(nombre="FR TOMATE X GR", precio=Decimal("3.80"), categoria=category)
        self.other = Producto.objects.create(nombre="MANZANA ROJA", precio=2000, categoria=category, codigo_de_barras="77012345")
        self.user = Usuario.objects.create_user("Cajero visor")

    def search(self, term, user=None):
        request = RequestFactory().get(reverse("visor_cajero_buscar"), {"term": term})
        request.user = user or self.user
        return ProductoBuscarVisorCajeroView.as_view()(request)

    def test_name_search_includes_products_without_barcode(self):
        data = json.loads(self.search("tomate").content)
        self.assertEqual(data["results"][0]["id"], self.product.pk)
        self.assertEqual(data["results"][0]["precio"], "3.80")
        self.assertEqual(data["results"][0]["barcode"], "")

    def test_exact_id_is_first_and_unrelated_products_are_not_returned(self):
        data = json.loads(self.search(str(self.other.pk)).content)
        self.assertEqual(data["results"][0]["id"], self.other.pk)
        self.assertEqual(len(data["results"]), 1)

    def test_words_can_be_in_different_order(self):
        self.assertEqual(json.loads(self.search("roja manzana").content)["results"][0]["id"], self.other.pk)

    def test_unknown_empty_and_oversized_ids_are_safe(self):
        for term in ("", "noexiste", "9" * 100):
            response = self.search(term)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.content)["results"], [])

    def test_anonymous_cannot_access_cashier_page_or_search(self):
        self.assertEqual(self.search("tomate", AnonymousUser()).status_code, 302)
        request = RequestFactory().get(reverse("visor_cajero"))
        request.user = AnonymousUser()
        self.assertEqual(VisorProductosCajeroView.as_view()(request).status_code, 302)

    def test_cashier_has_access_without_administration_permissions(self):
        self.assertTrue(user_can_access_url_name(self.user, "visor_cajero"))
        self.assertTrue(user_can_access_url_name(self.user, "visor_cajero_buscar"))

    def test_navbar_visor_opens_cashier_view_for_cashier_role(self):
        self.user.rolid = Rol.objects.create(nombre="Cajero")
        raw = {"label": "Visor Barcode", "url_name": "visor_barcode"}
        item = _resolve_nav_item(raw, self.user)
        self.assertEqual(item["url"], "/visor/cajeros/")
        self.assertEqual(raw["url_name"], "visor_barcode")

    def test_navbar_visor_opens_cashier_view_for_every_authenticated_account(self):
        raw = {"label": "Visor Barcode", "url_name": "visor_barcode"}
        for role_name in (None, "Web Master", "Administrador", "Empleado"):
            with self.subTest(role=role_name):
                self.user.rolid = Rol.objects.create(nombre=role_name) if role_name else None
                self.assertEqual(_resolve_nav_item(raw, self.user)["url"], reverse("visor_cajero"))
                self.assertEqual(raw["url_name"], "visor_barcode")

    def test_anonymous_navbar_keeps_public_visor_link(self):
        raw = {"label": "Visor Barcode", "url_name": "visor_barcode"}
        self.assertEqual(_resolve_nav_item(raw, AnonymousUser())["url"], reverse("visor_barcode"))

    def test_authenticated_public_visor_stays_barcode_only_without_redirect(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("visor_barcode"), follow=True)
        self.assertEqual(response.redirect_chain, [])
        self.assertNotContains(response, 'id="vb_search"')
        self.assertContains(response, 'id="vb_barcode"')
        self.assertContains(response, 'id="vb_camera"')
        self.assertContains(response, 'id="vb_quantity"')
        self.assertContains(response, 'const VISOR_CAJERO_URL = "";')
        self.assertNotContains(response, "Buscar por nombre o ID →")

    def test_public_barcode_lookup_does_not_fall_back_to_name_or_product_id(self):
        for authenticated in (False, True):
            if authenticated:
                self.client.force_login(self.user)
            for term in (self.other.nombre, str(self.product.pk)):
                with self.subTest(authenticated=authenticated, term=term):
                    response = self.client.get(reverse("visor_barcode_lookup"), {"barcode": term})
                    self.assertEqual(response.status_code, 404)
                    self.assertFalse(response.json()["success"])
            response = self.client.get(reverse("visor_barcode_lookup"), {"barcode": self.other.codigo_de_barras})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["product"]["id"], self.other.pk)

    def test_search_does_not_expose_internal_fields(self):
        item = json.loads(self.search("tomate").content)["results"][0]
        self.assertEqual(set(item), {"id", "text", "barcode", "precio", "precio_anterior"})

    def test_public_barcode_search_does_not_search_names(self):
        request = RequestFactory().get(reverse("visor_barcode_buscar"), {"term": "MANZANA", "page": "abc"})
        request.user = AnonymousUser()
        response = ProductoBuscarBarrasVisorView.as_view()(request)
        self.assertEqual(json.loads(response.content)["results"], [])

    def test_barcode_autocomplete_uses_one_query_and_prioritizes_exact_match(self):
        Producto.objects.create(nombre="Prefijo", precio=1, categoria=self.other.categoria, codigo_de_barras="7701234500")
        request = RequestFactory().get(reverse("visor_barcode_buscar"), {"term": "77012345"})
        with self.assertNumQueries(1):
            response = ProductoBuscarBarrasVisorView.as_view()(request)
        rows = json.loads(response.content)["results"]
        self.assertEqual(rows[0]["id"], self.other.pk)
        self.assertEqual(len(rows), 2)

    def test_public_page_has_quantity_but_not_name_search(self):
        request = RequestFactory().get(reverse("visor_barcode"))
        request.user = AnonymousUser()
        response = VisorProductoBarcodeView.as_view()(request)
        self.assertContains(response, 'id="vb_quantity"')
        self.assertContains(response, 'id="vb_barcode"')
        self.assertContains(response, 'const VISOR_CAJERO_URL = "";')
        self.assertNotContains(response, 'id="vb_search"')

    def test_cashier_page_has_both_search_modes(self):
        request = RequestFactory().get(reverse("visor_cajero"))
        request.user = self.user
        response = VisorProductosCajeroView.as_view()(request)
        for identifier in ("vb_search", "vb_barcode", "vb_quantity", "vb_camera"):
            self.assertContains(response, f'id="{identifier}"')


class VisorMarkupTests(SimpleTestCase):
    def test_both_autocompletes_share_fast_cache_and_safe_renderer(self):
        script = (settings.BASE_DIR / "mainApp/static/javascript/visor_barcode.js").read_text(encoding="utf-8")
        self.assertIn('enhanceAutocomplete($inp, VISOR_BARRAS_URL, "barcode")', script)
        self.assertIn('enhanceAutocomplete($search, VISOR_CAJERO_URL, "name")', script)
        self.assertIn("delay: 0, autoFocus: true, source", script)
        self.assertIn("Date.now() - saved.at < 5000", script)
        self.assertIn(".text(item.product.nombre)", script)
        self.assertIn('e.stopImmediatePropagation()', script)

    def test_local_autocomplete_assets_and_gram_instructions(self):
        template = (settings.BASE_DIR / "mainApp/templates/visor_producto_barcode.html").read_text(encoding="utf-8")
        self.assertIn("500 = medio kilo", template)
        self.assertNotIn("code.jquery.com", template)
        self.assertIn("vendor/jquery-ui/jquery-ui-1.13.2.min.js", template)

    def test_quantity_entry_does_not_lose_focus_periodically(self):
        script = (settings.BASE_DIR / "mainApp/static/javascript/visor_barcode.js").read_text(encoding="utf-8")
        self.assertNotIn("setInterval(forceFocus", script)
        self.assertNotIn('$(document).on("focusin"', script)
        self.assertIn("BigInt(quantity)", script)


class VisorNuevaVentaTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user(
            "Visor con turno", rolid=Rol.objects.create(nombre="Web Master"),
        )
        self.branch = Sucursal.objects.create(nombre="Sucursal visor")
        point = PuntosPago.objects.create(nombre="Caja visor", sucursalid=self.branch)
        self.turn = TurnoCaja.objects.create(cajero=self.user, puntopago=point)
        category = Categoria.objects.create(nombre="Plaza visor")
        self.product = Producto.objects.create(nombre="TOMATE POR GRAMO", precio="3.80", categoria=category)
        self.stock = Inventario.objects.create(productoid=self.product, sucursalid=self.branch, cantidad=10000)
        self.client.force_login(self.user)

    def transfer(self, **overrides):
        query = {"visor_producto": self.product.pk, "visor_cantidad": 500, "visor_turno": self.turn.pk}
        query.update(overrides)
        with patch("mainApp.views.is_feature_enabled", return_value=True):
            return self.client.get(reverse("generar_venta"), query)

    def test_active_shift_shows_new_cart_button_in_both_scanners(self):
        for route in ("visor_cajero", "visor_barcode"):
            response = self.client.get(reverse(route))
            self.assertContains(response, 'id="vb_new_sale"')
            self.assertContains(response, 'target="_blank" rel="noopener noreferrer"')
            self.assertContains(response, f'data-turno-id="{self.turn.pk}"')
            if route == "visor_barcode":
                self.assertNotContains(response, 'id="vb_search"')

    def test_button_absent_for_closed_or_closing_shift(self):
        for state in ("CIERRE", "CERRADO"):
            self.turn.estado = state
            self.turn.save(update_fields=["estado"])
            self.assertNotContains(self.client.get(reverse("visor_cajero")), 'id="vb_new_sale"')

    def test_other_cashier_and_anonymous_do_not_use_someone_elses_shift(self):
        other = Usuario.objects.create_user("Otro visor", rolid=self.user.rolid)
        self.client.force_login(other)
        self.assertNotContains(self.client.get(reverse("visor_cajero")), 'id="vb_new_sale"')
        self.client.logout()
        self.assertNotContains(self.client.get(reverse("visor_barcode")), 'id="vb_new_sale"')

    def test_active_shift_does_not_bypass_sales_permissions(self):
        with patch("mainApp.views.user_can_access_url_name", return_value=False):
            self.assertNotContains(self.client.get(reverse("visor_cajero")), 'id="vb_new_sale"')
            response = self.transfer()
            self.assertIsNone(response.context["producto_desde_visor"])
            self.assertContains(response, "No tienes permiso para generar ventas.")

    def test_weight_and_latest_price_are_seeded_without_writing_sale_or_stock(self):
        self.product.precio = Decimal("4.20")
        self.product.save(update_fields=["precio"])
        count = Venta.objects.count()
        response = self.transfer(precio="0.01", nombre="FALSO")
        self.assertEqual(response.status_code, 200)
        item = response.context["producto_desde_visor"]
        self.assertEqual(item["id"], str(self.product.pk))
        self.assertEqual(item["cantidad"], 500)
        self.assertEqual(item["precio_unitario"], "4.20")
        self.assertEqual(item["nombre"], self.product.nombre)
        self.assertEqual(Venta.objects.count(), count)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.cantidad, 10000)
        self.assertContains(response, 'id="venta-visor-producto"')

    def test_transfer_parameters_do_not_bind_the_sales_form(self):
        response = self.transfer()
        self.assertFalse(response.context["form"].is_bound)
        self.assertEqual(response.context["form"].initial["puntopago"], self.turn.puntopago_id)

    def test_stale_or_foreign_shift_is_rejected(self):
        response = self.transfer(visor_turno=self.turn.pk + 1)
        self.assertIsNone(response.context["producto_desde_visor"])
        self.assertContains(response, "El turno del visor ya no está abierto")

    def test_closed_shift_redirects_to_shift_page(self):
        self.turn.estado = "CIERRE"
        self.turn.save(update_fields=["estado"])
        response = self.transfer()
        self.assertRedirects(response, reverse("turno_caja"), fetch_redirect_response=False)

    def test_optional_shifts_do_not_allow_transfer_from_a_closed_shift(self):
        self.turn.estado = "CERRADO"
        self.turn.save(update_fields=["estado"])
        with patch("mainApp.views.is_feature_enabled", return_value=False):
            response = self.client.get(reverse("generar_venta"), {
                "visor_producto": self.product.pk, "visor_cantidad": 1, "visor_turno": self.turn.pk,
            })
        self.assertIsNone(response.context["producto_desde_visor"])
        self.assertContains(response, "El turno del visor ya no está abierto")

    def test_invalid_quantities_and_product_ids_never_preload(self):
        for value in ("0", "-1", "0.5", "500g", "1000001", "9" * 100, ""):
            with self.subTest(quantity=value):
                response = self.transfer(visor_cantidad=value)
                self.assertIsNone(response.context["producto_desde_visor"])
        for value in ("-1", "abc", "2147483648", "9" * 100):
            with self.subTest(product=value):
                response = self.transfer(visor_producto=value)
                self.assertIsNone(response.context["producto_desde_visor"])

    def test_missing_product_and_insufficient_stock_are_explained(self):
        response = self.transfer(visor_producto=self.product.pk + 10)
        self.assertContains(response, "no está en el inventario")
        response = self.transfer(visor_cantidad=10001)
        self.assertIsNone(response.context["producto_desde_visor"])
        self.assertContains(response, "Disponible: 10000")

    def test_ptm_cannot_be_sent_as_merchandise(self):
        self.product.tipo_ptm = "retiro"
        self.product.save(update_fields=["tipo_ptm"])
        response = self.transfer()
        self.assertIsNone(response.context["producto_desde_visor"])
        self.assertContains(response, "PTM se registra en Operaciones PTM")
