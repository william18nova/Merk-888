"""Regression checks for the normal cloud app, with a disposable database."""
import json
from decimal import Decimal
from uuid import uuid4

from django.apps import apps
from django.conf import settings
from django.contrib.messages import get_messages
from django.db import transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import (Cliente, ClienteEspecial, ConfiguracionFuncionalidad, DetalleVenta,
                     Egreso, ReintegroVenta, SolicitudPOS, TurnoCaja)
from .services.feature_flags import clear_feature_cache, TURN_REQUIRED_FEATURE
from .services.special_discount import generate_one_time_code, preview_one_time_code, lock_one_time_code
from .test_pos_request_receipts import ReceiptFixture


@override_settings(DEBUG=False, SECRET_KEY="fictional-cloud-regression-only")
class ProductionCompatibilityTests(ReceiptFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)

    def test_production_does_not_enable_local_runtime(self):
        self.assertFalse(apps.is_installed("local_pos"))
        self.assertNotIn("offline", settings.DATABASES)
        self.assertFalse(getattr(settings, "NOVA_INTEGRATED_POS_ENABLED", False))
        self.assertFalse(getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False))

    def test_normal_pages_render_without_offline_urls_or_scripts(self):
        ConfiguracionFuncionalidad.objects.filter(clave=TURN_REQUIRED_FEATURE).update(habilitada=True)
        clear_feature_cache()
        TurnoCaja.objects.create(puntopago=self.point, cajero=self.user)
        for name in ("home", "generar_venta", "turno_caja", "registrar_egreso",
                     "claves_descuento_merk2888", "visualizar_productos", "metricas_negocio"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "javascript/integrated_business_transport.js")
                self.assertNotContains(response, "javascript/integrated_navigation.js")

    def test_online_autocomplete_uses_regular_catalog(self):
        Cliente.objects.create(nombre="Cloud", apellido="Customer", numerodocumento="FICTIONAL-CLOUD")
        response = self.client.get(reverse("cliente_autocomplete"), {"q": "Cloud"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["results"])

    def test_online_special_code_still_validates_without_offline_reservation(self):
        customer = Cliente.objects.create(nombre="merk2888", apellido="", numerodocumento="FICTIONAL-SPECIAL")
        ClienteEspecial.objects.create(cliente=customer)
        authorization, code = generate_one_time_code(self.user, uuid4().hex)
        self.assertEqual(preview_one_time_code(customer, code, actor=self.user).pk, authorization.pk)
        with transaction.atomic():
            self.assertEqual(lock_one_time_code(customer, code, authorization.pk).pk, authorization.pk)
        self.assertFalse(SolicitudPOS.objects.exists())

    def test_online_expense_does_not_require_integrated_headers(self):
        response = self.client.post(reverse("registrar_egreso"), {
            "concepto": "cloud test", "monto": "1.000", "medio_pago": "efectivo",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Egreso.objects.get().monto, Decimal("1000"))
        self.assertFalse(SolicitudPOS.objects.exists())

    def test_online_open_sell_refund_close_preserves_original_flow(self):
        ConfiguracionFuncionalidad.objects.filter(clave=TURN_REQUIRED_FEATURE).update(habilitada=True)
        clear_feature_cache()
        response = self.client.post(reverse("turno_caja_iniciar"), {
            "puntopago_id": str(self.point.pk), "cajero_id": str(self.user.pk),
            "password": "test-only", "saldo_apertura_efectivo": "1000",
        })
        self.assertEqual(response.status_code, 200, response.content)
        turn_id = response.json()["turno_id"]
        sale = json.loads(self.sale(self.request()).content)
        self.assertTrue(sale["success"], sale)
        detail = DetalleVenta.objects.get(ventaid_id=sale["venta_id"])
        refund = self.client.post(reverse("ver_venta", kwargs={"venta_id": sale["venta_id"]}), {
            "mediopago": "efectivo", "accion": "guardar",
            "dev-TOTAL_FORMS": "1", "dev-INITIAL_FORMS": "0",
            "dev-0-detalle_id": str(detail.pk), "dev-0-devolver": "1",
            "reint-TOTAL_FORMS": "1", "reint-INITIAL_FORMS": "0",
            "reint-0-medio_pago": "efectivo", "reint-0-monto": "2000",
        })
        self.assertEqual(refund.status_code, 302)
        self.assertTrue(ReintegroVenta.objects.exists(), (refund.url, [str(message) for message in get_messages(refund.wsgi_request)]))
        self.assertEqual(ReintegroVenta.objects.get().monto, Decimal("2000"))
        begin = self.client.post(reverse("turno_caja_iniciar_cierre"), {"turno_id": turn_id})
        self.assertEqual(begin.status_code, 200, begin.content)
        closed = self.client.post(reverse("turno_caja_cerrar"), {
            "turno_id": turn_id, "efectivo_entregado": "3000",
            "facturas_pagadas": "0", "medios_json": "[]", "ptm_transacciones": "0",
        })
        self.assertEqual(closed.status_code, 200, closed.content)
        self.assertEqual(TurnoCaja.objects.get(pk=turn_id).estado, "CERRADO")
        self.assertEqual(TurnoCaja.objects.get(pk=turn_id).diferencia_total, Decimal("0"))
        self.assertFalse(SolicitudPOS.objects.exists())
