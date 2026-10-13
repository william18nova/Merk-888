import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier, Event
from unittest.mock import patch
from uuid import uuid4

from django.db import connection, connections
from django.http import JsonResponse, HttpResponse
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from .models import (Categoria, ConfiguracionFuncionalidad, Empleado, Inventario, MetodoPago,
                     Producto, PuntosPago, Rol, SolicitudPOS, Sucursal, Usuario, Venta)
from .services.feature_flags import clear_feature_cache, TURN_REQUIRED_FEATURE
from .services.pos_request_receipts import protect_sale, request_fingerprint, recover_receipt
from .views import GenerarVentaView


class ReceiptFixture:
    def setUp(self):
        self.role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("receipt-test", "test-only", rolid=self.role)
        self.branch = Sucursal.objects.create(nombre="Receipt test")
        self.point = PuntosPago.objects.create(nombre="Test", sucursalid=self.branch)
        self.employee = Empleado.objects.create(usuarioid=self.user, sucursalid=self.branch,
            nombre="Prueba", apellido="Recibo", numerodocumento="TEST-RECEIPT")
        category = Categoria.objects.create(nombre="Test")
        self.product = Producto.objects.create(nombre="Test rice", precio=Decimal("2000"), categoria=category)
        self.stock = Inventario.objects.create(sucursalid=self.branch, productoid=self.product, cantidad=0)
        MetodoPago.objects.create(codigo="efectivo", nombre="Efectivo", activo=True, es_efectivo=True)
        ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=False)
        clear_feature_cache()

    def request(self, opid=None, **changes):
        data = {"sucursal": self.branch.pk, "puntopago": self.point.pk,
                "productos": json.dumps([self.product.pk]), "cantidades": "[2]",
                "medio_pago": "efectivo", "pagos": '[{"medio_pago":"efectivo","monto":"4000"}]',
                "efectivo_recibido": "5000", **changes}
        request = RequestFactory().post("/generar_venta/", data,
            **({"HTTP_X_NOVA_OPERATION_ID": str(opid)} if opid is not None else {}))
        request.user = self.user
        return request

    def sale(self, request):
        return GenerarVentaView().post(request)


@override_settings(SECRET_KEY="pos-original-receipt-test", DEBUG=False)
class RequestReceiptTests(ReceiptFixture, TestCase):
    def test_original_sale_replayed_after_lost_ack_creates_one_invoice(self):
        operation_id = uuid4()
        first = self.sale(self.request(operation_id))
        data = json.loads(first.content)
        self.assertTrue(data["success"], data)
        replay = self.sale(self.request(operation_id, csrfmiddlewaretoken="new-csrf"))
        self.assertEqual(json.loads(replay.content)["venta_id"], data["venta_id"])
        self.assertEqual(replay["X-Nova-Replayed"], "1")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(SolicitudPOS.objects.count(), 1)
        self.stock.refresh_from_db()
        self.point.refresh_from_db()
        self.assertEqual(self.stock.cantidad, -2)
        self.assertEqual(self.point.dinerocaja, Decimal("4000"))
        confirmed = recover_receipt(self.user, operation_id)
        self.assertEqual(confirmed["status"], "accepted")
        self.assertEqual(confirmed["response"]["venta_id"], data["venta_id"])

    def test_absent_receipt_is_only_returned_after_a_successful_cloud_read(self):
        operation_id = uuid4()
        self.assertEqual(recover_receipt(self.user, operation_id)["status"], "absent")
        with patch("mainApp.services.pos_request_receipts.lock_operation", side_effect=OSError("connection lost")):
            with self.assertRaises(OSError):
                recover_receipt(self.user, operation_id)

    def test_same_uuid_cannot_be_used_for_a_different_cart(self):
        operation_id = uuid4()
        self.sale(self.request(operation_id))
        response = self.sale(self.request(operation_id, cantidades="[1]"))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Venta.objects.count(), 1)

    def test_receipt_is_not_visible_to_another_user(self):
        operation_id = uuid4()
        self.sale(self.request(operation_id))
        request = self.request(operation_id)
        request.user = Usuario.objects.create_user("other-user", "test-only", rolid=self.role)
        response = self.sale(request)
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("receipt_text", json.loads(response.content))
        with self.assertRaises(PermissionError):
            recover_receipt(request.user, operation_id)

    def test_no_header_preserves_existing_sale_flow(self):
        response = self.sale(self.request())
        self.assertTrue(json.loads(response.content)["success"])
        self.assertFalse(SolicitudPOS.objects.exists())

    @override_settings(NOVA_INTEGRATED_POS_ENABLED=True)
    def test_integrated_mode_rejects_old_javascript_without_reference(self):
        response = self.sale(self.request())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)["code"], "operation_required")
        self.assertFalse(Venta.objects.exists())

    def test_two_independent_carts_are_two_sales(self):
        first = json.loads(self.sale(self.request(uuid4())).content)
        second = json.loads(self.sale(self.request(uuid4())).content)
        self.assertNotEqual(first["venta_id"], second["venta_id"])
        self.assertEqual(SolicitudPOS.objects.count(), 2)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.cantidad, -4)

    def test_inactive_user_cannot_create_or_recover_a_receipt(self):
        operation_id = uuid4()
        self.sale(self.request(operation_id))
        self.user.is_active = False
        self.assertEqual(self.sale(self.request(operation_id)).status_code, 401)
        with self.assertRaises(PermissionError):
            recover_receipt(self.user, operation_id)

    def test_validation_failure_can_be_corrected_using_same_uuid(self):
        operation_id = uuid4()
        response = self.sale(self.request(operation_id, productos="[]", cantidades="[]"))
        self.assertFalse(json.loads(response.content)["success"])
        self.assertFalse(SolicitudPOS.objects.exists())
        self.assertTrue(json.loads(self.sale(self.request(operation_id)).content)["success"])

    def test_invalid_id_rejected_before_any_sale(self):
        response = self.sale(self.request("invalid-id"))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Venta.objects.exists())

    def test_form_secrets_are_not_stored_and_change_the_keyed_fingerprint(self):
        one = self.request(uuid4(), empleado_password="secret-not-to-persist")
        two = self.request(uuid4(), empleado_password="different-secret")
        self.assertNotEqual(request_fingerprint(one), request_fingerprint(two))
        self.assertEqual(len(request_fingerprint(one)), 64)
        self.sale(self.request(uuid4()))
        receipt = SolicitudPOS.objects.get()
        self.assertNotIn("print_token", receipt.respuesta)
        self.assertEqual({f.name for f in receipt._meta.fields},
                         {"id", "usuario", "accion", "huella", "venta", "respuesta", "creada_en"})

    def test_failed_or_non_json_response_rolls_back_any_partial_writes(self):
        for response in (JsonResponse({"success": False}), HttpResponse("failure")):
            @protect_sale
            def bad_view(_self, _request):
                Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("9999"))
                return response
            bad_view(None, self.request(uuid4()))
            self.product.refresh_from_db()
            self.assertEqual(self.product.precio, Decimal("2000"))
            self.assertFalse(SolicitudPOS.objects.exists())

    def test_receipt_and_sale_rollback_on_unexpected_failure(self):
        @protect_sale
        def bad_view(_self, _request):
            Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("9999"))
            raise RuntimeError("simulated crash before commit")
        with self.assertRaises(RuntimeError):
            bad_view(None, self.request(uuid4()))
        self.product.refresh_from_db()
        self.assertEqual(self.product.precio, Decimal("2000"))
        self.assertFalse(SolicitudPOS.objects.exists())


@override_settings(SECRET_KEY="pos-original-receipt-test", DEBUG=False)
class ConcurrentReceiptTests(ReceiptFixture, TransactionTestCase):
    def test_recovery_waits_for_inflight_sale_commit_instead_of_reporting_absent(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL advisory transaction locks")
        operation_id, writing, release, reading = uuid4(), Event(), Event(), Event()
        original = GenerarVentaView.post.__wrapped__

        @protect_sale
        def slow_sale(view, request):
            response = original(view, request)
            writing.set()
            if not release.wait(timeout=10):
                raise RuntimeError("test synchronization timed out")
            return response

        def save():
            try:
                return slow_sale(GenerarVentaView(), self.request(operation_id))
            finally:
                connections.close_all()

        def recover():
            try:
                reading.set()
                return recover_receipt(self.user, operation_id)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            sale = executor.submit(save)
            try:
                self.assertTrue(writing.wait(timeout=10))
                recovery = executor.submit(recover)
                self.assertTrue(reading.wait(timeout=10))
                # It must not conclude 'absent' while the sale owns the lock.
                from concurrent.futures import TimeoutError
                with self.assertRaises(TimeoutError):
                    recovery.result(timeout=0.15)
            finally:
                release.set()
            self.assertTrue(json.loads(sale.result(timeout=10).content)["success"])
            self.assertEqual(recovery.result(timeout=10)["status"], "accepted")

    def test_two_simultaneous_requests_create_one_sale(self):
        if connection.vendor != "postgresql":
            self.skipTest("Requires PostgreSQL row/unique constraint concurrency")
        operation_id = uuid4()
        barrier = Barrier(2)
        def sell():
            try:
                user = Usuario.objects.get(pk=self.user.pk)
                request = self.request(operation_id)
                request.user = user
                barrier.wait(timeout=10)
                return json.loads(self.sale(request).content)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: sell(), range(2)))
        self.assertTrue(all(r["success"] for r in results), results)
        self.assertEqual(results[0]["venta_id"], results[1]["venta_id"])
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(SolicitudPOS.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.cantidad, -2)
