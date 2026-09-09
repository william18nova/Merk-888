import json
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from .models import Categoria, Producto, Usuario, Rol
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

    def test_authenticated_old_visor_url_redirects_directly_to_cashier_page(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("visor_barcode"), follow=True)
        self.assertEqual(response.redirect_chain, [(reverse("visor_cajero"), 302)])
        self.assertContains(response, 'id="vb_search"')
        self.assertContains(response, 'id="vb_barcode"')
        self.assertNotContains(response, "Buscar por nombre o ID →")

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
