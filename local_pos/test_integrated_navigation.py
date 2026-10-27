"""Fresh requests/tabs with local sessions and real, fictional PostgreSQL data."""
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.db import connections
from django.test import Client, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from mainApp.models import Producto
from .connectivity import Health
from .models import IntegratedIdentity, LocalCommand
from .test_integrated_business import IntegratedBusinessTests
from .test_integrated_replica import IntegratedReplicaTests


@override_settings(NOVA_REFERENCE_ENABLED=True, NOVA_INTEGRATED_JOURNAL_ENABLED=True,
    ROOT_URLCONF="local_pos.test_integrated_journal", DEBUG=False)
class IntegratedNavigationTests(TransactionTestCase):
    databases = {"default", "offline"}
    sync = IntegratedReplicaTests.sync

    def _fixture_teardown(self):
        with override_settings(DATABASE_ROUTERS=[]):
            super()._fixture_teardown()

    def setUp(self):
        IntegratedBusinessTests.setUp(self)
        self.client.force_login(self.user, backend="local_pos.integrated_auth.IntegratedBackend")
        self.health = patch("local_pos.integrated_middleware.monitor.snapshot", return_value=Health.DISCONNECTED).start()
        self.addCleanup(patch.stopall)

    def test_real_offline_routes_open_next_turn_and_render_close_steps(self):
        from uuid import uuid4
        from django.urls import reverse
        def post(name, data):
            return self.client.post(reverse(name), data, HTTP_X_NOVA_OPERATION_ID=str(uuid4()))
        self.assertEqual(post("turno_caja_iniciar_cierre", {"turno_id": self.turn.pk}).status_code, 200)
        self.assertEqual(post("turno_caja_cerrar", {"turno_id": self.turn.pk,
            "efectivo_entregado": "6000", "medios_json": "[]", "facturas_pagadas": "0"}).status_code, 200)
        page = self.client.get(reverse("turno_caja"))
        self.assertEqual(page.status_code, 200)
        for name in ("turno_caja_puntopago_ac", "turno_caja_cajero_ac"):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, response.content)
            self.assertTrue(response.json()["results"])
        opened = post("turno_recuperar_o_iniciar", {"action": "recuperar_o_iniciar",
            "usuario_id": self.user.pk, "puntopago_id": self.point.pk,
            "password": "fictional-only", "saldo_apertura_efectivo": "1000"})
        self.assertEqual(opened.status_code, 200, opened.content)
        turn_id = opened.json()["turno_id"]
        self.assertEqual(self.client.get(reverse("generar_venta")).status_code, 200)
        self.assertEqual(post("turno_caja_iniciar_cierre", {"turno_id": turn_id}).status_code, 200)
        for name in ("turno_caja_cierre_pagos", "turno_caja_cierre_efectivo", "turno_caja_cierre_medios"):
            response = self.client.get(reverse(name, args=[turn_id]))
            self.assertEqual(response.status_code, 200, response.content[:250])

    def test_offline_employee_autocomplete_flags_without_secret_exposure(self):
        from django.urls import reverse
        buyer, employee, client = IntegratedBusinessTests.prepare_employee_discount(self)
        response = self.client.get(reverse("cliente_autocomplete"), {"q": "87123401", "term": "87123401"})
        self.assertEqual(response.status_code, 200, response.content)
        row = next(row for row in response.json()["results"] if row["id"] == client.pk)
        self.assertTrue(row["is_employee"])
        self.assertTrue(row["employee_has_user"])
        self.assertNotIn(buyer.password.encode(), response.content)
        self.assertNotIn(b"buyer-password", response.content)

    def test_offline_close_checkpoint_uses_only_local_database(self):
        import json
        from uuid import uuid4
        from django.urls import reverse
        from mainApp.models import AvanceCierreCaja
        self.assertEqual(self.client.post(reverse('turno_caja_iniciar_cierre'), {'turno_id': self.turn.pk},
            HTTP_X_NOVA_OPERATION_ID=str(uuid4())).status_code, 200)
        url = reverse('turno_caja_cierre_respaldo', args=[self.turn.pk])
        with CaptureQueriesContext(connections['default']) as cloud:
            response = self.client.post(url, json.dumps({'step': 'cash', 'invoices': '0',
                'payment_details': [], 'denominations': None}), content_type='application/json')
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(self.client.get(url).json()['progress']['step'], 'cash')
            self.assertEqual(len(cloud), 0)
        self.assertTrue(AvanceCierreCaja.objects.using('offline').filter(turno_id=self.turn.pk).exists())
        self.assertFalse(AvanceCierreCaja.objects.using('default').exists())

    def test_new_tab_opens_original_home_and_sale_without_cloud_or_page_cache(self):
        tab = Client()
        tab.cookies = self.client.cookies.copy()
        with CaptureQueriesContext(connections["default"]) as cloud:
            for url, template in (("/home/", "homePage.html"), ("/generar_venta/", "generar_venta.html")):
                response = tab.get(url)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertTemplateUsed(response, template)
                self.assertContains(response, 'class="nv"')
                self.assertContains(response, "integrated_navigation.js")
                self.assertEqual(response["X-Nova-Mode"], "offline")
            self.assertEqual(len(cloud), 0)
        home = tab.get("/home/")
        self.assertContains(home, "indicadores generales necesitan internet")
        self.assertNotContains(home, "Ventas de hoy")
        self.assertFalse(LocalCommand.objects.using("offline").exists())

    def test_unsupported_direct_url_retains_menu_without_running_business_view(self):
        with patch("mainApp.views.HomePageView._dashboard_permissions", side_effect=AssertionError("must not calculate KPIs")):
            self.assertEqual(self.client.get("/home/").status_code, 200)
        response = self.client.get("/agregar_producto/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "local_pos/offline_unavailable.html")
        self.assertContains(response, "Estamos offline")
        self.assertContains(response, 'class="nv"')
        self.assertEqual(response["X-Nova-Offline-Blocked"], "1")

    def test_unavailable_post_and_ajax_still_fail_without_creating_data(self):
        before = Producto.objects.using("offline").count()
        for method, headers in (("post", {}), ("post", {"HTTP_ACCEPT": "application/json"}),
                                ("get", {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}),
                                ("get", {"HTTP_SEC_FETCH_DEST": "empty"})):
            response = getattr(self.client, method)("/agregar_producto/", {"nombre": "MUST NOT SAVE"}, **headers)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response["X-Nova-Offline-Blocked"], "1")
        self.assertEqual(Producto.objects.using("offline").count(), before)
        self.assertFalse(LocalCommand.objects.using("offline").exists())

    def test_navigation_preflight_offline_then_online_and_external_path_rejected(self):
        for path, allowed in (("/home/", True), ("/generar_venta/", True), ("/local/sincronizacion/", True),
                              ("/visor/cajeros/", True), ("/agregar_producto/", False)):
            response = self.client.get("/local/disponibilidad/", {"path": path})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["allowed"], allowed)
            self.assertTrue(response.json()["offline"])
        self.assertEqual(self.client.get("/local/disponibilidad/", {"path": "https://example.com/"}).status_code, 400)
        self.health.return_value = Health.AVAILABLE
        response = self.client.get("/local/disponibilidad/", {"path": "/agregar_producto/"})
        self.assertTrue(response.json()["allowed"])
        self.assertFalse(response.json()["offline"])

    def test_fresh_catalog_view_and_search_use_copied_products(self):
        with CaptureQueriesContext(connections["default"]) as cloud:
            response = self.client.get("/visor/cajeros/")
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "visor_producto_barcode.html")
            result = self.client.get("/visor/cajeros/buscar/", {"term": "FICTIONAL"}).json()
            self.assertEqual(result["results"][0]["id"], self.product.pk)
            self.assertEqual(len(cloud), 0)

    def test_expired_authorization_does_not_gain_access_from_navigation(self):
        IntegratedIdentity.objects.using("offline").update(expires_at=timezone.now() - timedelta(seconds=1))
        for url in ("/home/", "/generar_venta/", "/local/disponibilidad/"):
            self.assertEqual(self.client.get(url).status_code, 503)
