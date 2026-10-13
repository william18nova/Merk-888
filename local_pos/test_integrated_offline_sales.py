"""Original UI contract, real isolated PostgreSQL databases, fictional sales."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError, connections
from django.test import TransactionTestCase, override_settings, RequestFactory
from django.utils import timezone

from mainApp.models import (ConfiguracionFuncionalidad, Venta, DetalleVenta, SolicitudPOS,
    Inventario, Producto, PuntosPago, TurnoCaja, Usuario, MetodoPago)
from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE, clear_feature_cache
from mainApp.views import GenerarVentaView
from .test_integrated_replica import IntegratedReplicaTests
from .models import LocalCommand, ReplicaState, ReplicaRow, LocalNode
from .integrated_offline_sales import flush, upload, KIND
from .integrated_journal import prepare_intent, request_fingerprint
from .request_context import database_context
from .connectivity import Health


@override_settings(NOVA_REFERENCE_ENABLED=True, NOVA_INTEGRATED_JOURNAL_ENABLED=True, DEBUG=False,
    ROOT_URLCONF="local_pos.test_integrated_journal")
class IntegratedOfflineSaleTests(TransactionTestCase):
    databases = {"default", "offline"}
    sync = IntegratedReplicaTests.sync

    def _fixture_teardown(self):
        with override_settings(DATABASE_ROUTERS=[]):
            super()._fixture_teardown()

    def setUp(self):
        IntegratedReplicaTests.setUp(self)
        ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=True)
        clear_feature_cache()
        self.turn = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point)
        self.sync()

    def request(self, operation_id=None, **changes):
        data = {"sucursal": self.branch.pk, "puntopago": self.point.pk,
            "productos": json.dumps([self.product.pk]), "cantidades": "[2]", "medio_pago": "efectivo",
            "pagos": '[{"medio_pago":"efectivo","monto":"4000"}]', "efectivo_recibido": "5000", **changes}
        request = RequestFactory().post("/generar_venta/", data, HTTP_X_NOVA_OPERATION_ID=str(operation_id or uuid4()),
            HTTP_X_NOVA_EXPECTED_TOTAL="4000")
        request.user = Usuario.objects.using("offline").get(pk=self.user.pk)
        return request

    def save(self, request=None):
        request = request or self.request()
        with database_context("offline"):
            response = GenerarVentaView().post(request)
        return json.loads(response.content)

    def test_local_atomic_commit_then_upload_and_refresh_negative_stock_once(self):
        request = self.request()
        first = self.save(request)
        self.assertTrue(first["success"], first)
        self.assertTrue(first["offline_saved"])
        self.assertIn("CAMBIO", first["receipt_text"])
        self.assertEqual(self.save(request)["venta_id"], first["venta_id"])
        self.assertFalse(Venta.objects.exists())
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -52)
        self.assertEqual(flush(), 1)
        self.assertEqual(flush(), 0)
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Inventario.objects.get(sucursalid=self.branch).cantidad, -52)
        self.assertEqual(PuntosPago.objects.get(pk=self.point.pk).dinerocaja, 16345)
        self.assertEqual(SolicitudPOS.objects.get().pk, LocalCommand.objects.using("offline").get().pk)
        self.sync()
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -52)
        self.assertFalse(ReplicaState.objects.using("offline").get().scope.get("refresh_required"))
        # Even after switching online a delayed browser retry cannot make another sale.
        self.assertEqual(json.loads(GenerarVentaView().post(request).content)["venta_id"], first["venta_id"])
        self.assertEqual(Venta.objects.count(), 1)

    def test_local_failure_rolls_back_command_sequence_and_inventory(self):
        with patch.object(ReplicaRow, "save", side_effect=DatabaseError("fixture local disk failure")):
            with self.assertRaises(DatabaseError):
                self.save()
        self.assertFalse(LocalCommand.objects.using("offline").exists())
        self.assertEqual(LocalNode.objects.using("offline").get().sequence, 0)
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -50)

    def test_cloud_ack_lost_retries_same_receipt_without_second_sale(self):
        self.assertTrue(self.save()["success"])
        command = LocalCommand.objects.using("offline").get()
        first = upload(command)  # Cloud committed; simulate process death before local ACK.
        self.assertEqual(command.state, "pending")
        self.assertEqual(flush(), 1)
        self.assertEqual(LocalCommand.objects.using("offline").get().local_result["cloud_response"]["venta_id"], first["venta_id"])
        self.assertEqual(Venta.objects.count(), 1)

    def test_network_failure_keeps_durable_pending_and_allows_more_sales(self):
        self.assertTrue(self.save()["success"])
        with patch("local_pos.integrated_offline_sales.upload", side_effect=DatabaseError("fixture network")):
            with self.assertRaises(DatabaseError):
                flush()
        connections.close_all()  # No dependence on process memory.
        self.assertTrue(self.save()["success"])
        self.assertEqual(LocalCommand.objects.using("offline").filter(state="pending").count(), 2)
        self.assertEqual(flush(), 2)
        self.assertEqual(Venta.objects.count(), 2)
        self.assertEqual(Inventario.objects.get(sucursalid=self.branch).cantidad, -54)

    def test_price_change_does_not_change_already_paid_total_or_timestamp(self):
        self.assertTrue(self.save()["success"])
        command = LocalCommand.objects.using("offline").get()
        Producto.objects.filter(pk=self.product.pk).update(precio=3000)
        self.assertEqual(flush(), 1)
        self.assertEqual(DetalleVenta.objects.get().preciounitario, 2000)
        self.assertEqual(Venta.objects.get().total, 4000)
        self.sync()
        self.assertEqual(Producto.objects.using("offline").get().precio, 3000)

    def test_closed_turn_preserves_paid_sale_for_review(self):
        self.assertTrue(self.save()["success"])
        TurnoCaja.objects.filter(pk=self.turn.pk).update(estado="CERRADO")
        self.assertEqual(flush(), 0)
        command = LocalCommand.objects.using("offline").get()
        self.assertEqual(command.state, "conflict")
        self.assertTrue(command.local_result["response"]["offline_saved"])
        self.assertFalse(Venta.objects.exists())
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -52)

    def test_revoked_permission_and_tampering_do_not_create_cloud_sales(self):
        self.save()
        Usuario.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(flush(), 0)
        self.assertFalse(Venta.objects.exists())

    def test_tampered_payload_is_not_uploaded(self):
        self.save()
        command = LocalCommand.objects.using("offline").get()
        command.payload["total"] = "1"
        command.save(using="offline", update_fields=["payload"])
        self.assertEqual(flush(), 0)
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "conflict")
        self.assertFalse(Venta.objects.exists())

    def test_uncertain_online_reference_cannot_be_reinterpreted_offline(self):
        request = self.request()
        from uuid import UUID
        prepare_intent(self.user, UUID(request.headers["X-Nova-Operation-ID"]), request_fingerprint(request))
        self.assertFalse(self.save(request)["success"])
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -50)

    def test_invalid_or_unauthorized_carts_do_not_change_inventory(self):
        variants = [dict(cantidades="[-2]"), dict(cantidades="[0]"), dict(productos="[9999999]"),
            dict(nequi_notificacion_id="1"), dict(empleado_password="secret"), dict(codigo_descuento_merk2888="1234"),
            dict(cliente_id="9999999"), dict(pagos='[{"medio_pago":"efectivo","monto":"1"}]'),
            dict(puntopago=self.other_point.pk), dict(efectivo_recibido="NaN"), dict(efectivo_recibido="10")]
        for data in variants:
            with self.subTest(data=data):
                self.assertFalse(self.save(self.request(**data))["success"])
        self.assertFalse(LocalCommand.objects.using("offline").exists())
        ReplicaState.objects.using("offline").update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(self.save()["success"])
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -50)

    def test_known_absent_online_attempt_can_be_saved_offline_but_not_twice(self):
        request = self.request()
        from uuid import UUID
        from .integrated_journal import record_result
        command = prepare_intent(self.user, UUID(request.headers["X-Nova-Operation-ID"]), request_fingerprint(request))
        record_result(command, "absent")
        self.assertTrue(self.save(request)["offline_saved"])
        self.assertEqual(LocalCommand.objects.using("offline").count(), 1)
        self.assertEqual(flush(), 1)
        self.assertEqual(Venta.objects.count(), 1)

    def test_cloud_database_failure_inside_business_writer_is_retryable(self):
        self.save()
        with patch.object(DetalleVenta.objects, "bulk_create", side_effect=DatabaseError("fixture network")):
            with self.assertRaises(DatabaseError):
                flush()
        self.assertFalse(Venta.objects.exists())
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "pending")
        self.assertEqual(flush(), 1)

    def test_grams_use_same_pricing_and_generic_client(self):
        Producto.objects.filter(pk=self.product.pk).update(precio="2.30")
        self.sync()
        request = self.request(cantidades="[500]", cliente_id=self.client_row.pk,
            pagos='[{"medio_pago":"efectivo","monto":"1150"}]', efectivo_recibido="2000")
        request.META["HTTP_X_NOVA_EXPECTED_TOTAL"] = "1150"
        result = self.save(request)
        self.assertTrue(result["success"], result)
        self.assertEqual(flush(), 1)
        self.assertEqual(DetalleVenta.objects.get().cantidad, 500)
        self.assertEqual(Venta.objects.get().total, 1150)

    def test_no_turn_configuration_is_preserved(self):
        ConfiguracionFuncionalidad.objects.filter(clave=TURN_REQUIRED_FEATURE).update(habilitada=False)
        clear_feature_cache()
        self.sync()
        self.assertTrue(self.save()["success"])
        self.assertEqual(flush(), 1)

    def test_nequi_is_explicitly_excluded_from_offline_sales(self):
        MetodoPago.objects.create(codigo="nequi", nombre="Nequi", activo=True)
        self.sync()
        result = self.save(self.request(pagos='[{"medio_pago":"efectivo","monto":"1000"},{"medio_pago":"nequi","monto":"3000"}]'))
        self.assertFalse(result["success"], result)
        self.assertIn("Nequi", result["error"])
        self.assertFalse(LocalCommand.objects.using("offline").exists())

    def test_original_page_and_form_post_work_through_offline_middleware(self):
        self.client.force_login(self.user)
        request = self.request()
        with patch("local_pos.integrated_middleware.monitor.snapshot", return_value=Health.DISCONNECTED):
            page = self.client.get("/generar_venta/")
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, "Generar Venta")
            from django.urls import reverse
            check = self.client.post(reverse("verificar_producto"), {"producto_id": self.product.pk,
                "sucursal_id": self.branch.pk, "cantidad": 2})
            self.assertEqual(check.status_code, 200)
            self.assertEqual(Decimal(str(check.json()["precio_unitario"])), 2000)
            response = self.client.post("/generar_venta/", request.POST,
                HTTP_X_NOVA_OPERATION_ID=request.headers["X-Nova-Operation-ID"], HTTP_X_NOVA_EXPECTED_TOTAL="4000")
            self.assertTrue(response.json()["success"], response.content)
            status = self.client.get(f'/local/ventas/confirmacion/{request.headers["X-Nova-Operation-ID"]}/')
            self.assertEqual(status.json()["status"], "saved_offline")
            summary = self.client.get("/local/ventas/estado/").json()
            self.assertEqual(summary["pending"], 1)
            self.assertEqual(summary["mode"], "offline")
        # During upload, new carts still go to the local journal, not cloud.
        with patch("local_pos.integrated_middleware.monitor.snapshot", return_value=Health.AVAILABLE):
            another = self.request()
            response = self.client.post("/generar_venta/", another.POST,
                HTTP_X_NOVA_OPERATION_ID=another.headers["X-Nova-Operation-ID"], HTTP_X_NOVA_EXPECTED_TOTAL="4000")
            self.assertTrue(response.json()["offline_saved"], response.content)
        self.assertFalse(Venta.objects.exists())
