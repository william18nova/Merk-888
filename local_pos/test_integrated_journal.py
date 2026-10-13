"""Original business view, durable local intent and read-only cloud recovery."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.db import connections, DatabaseError
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.urls import path, include
from django.http import Http404

from mainApp.models import ConfiguracionFuncionalidad, Venta, SolicitudPOS, Inventario, PuntosPago, Usuario
from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE, clear_feature_cache
from mainApp.views import GenerarVentaView
from .test_integrated_replica import IntegratedReplicaTests
from .integrated_journal import (KIND, prepare_intent, operation_lock, reconcile_pending,
    protect_original_request, request_fingerprint, read_cloud_confirmation)
from .models import LocalCommand, ReplicaState
from .request_context import database_context
from .journal_views import confirmation

urlpatterns = [path("", include("local_pos.journal_views")), path("", include("local_pos.reference_views")), path("", include("local_pos.integrated_review")), path("", include("local_pos.integrated_navigation")), path("", include("mainApp.urls"))]


@override_settings(NOVA_REFERENCE_ENABLED=True, NOVA_INTEGRATED_JOURNAL_ENABLED=True, DEBUG=False,
    ROOT_URLCONF=__name__)
class IntegratedJournalTests(TransactionTestCase):
    databases = {"default", "offline"}
    sync = IntegratedReplicaTests.sync

    def _fixture_teardown(self):
        with override_settings(DATABASE_ROUTERS=[]):
            super()._fixture_teardown()

    def setUp(self):
        IntegratedReplicaTests.setUp(self)
        self.sync()
        ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=False)
        clear_feature_cache()

    def request(self, operation_id=None, **changes):
        data = {"sucursal": self.branch.pk, "puntopago": self.point.pk,
            "productos": json.dumps([self.product.pk]), "cantidades": "[2]", "medio_pago": "efectivo",
            "pagos": '[{"medio_pago":"efectivo","monto":"4000"}]', "efectivo_recibido": "5000", **changes}
        request = RequestFactory().post("/generar_venta/", data, HTTP_X_NOVA_OPERATION_ID=str(operation_id or uuid4()))
        request.user = self.user
        return request

    def sale(self, request):
        return GenerarVentaView().post(request)

    def test_intent_is_durable_before_any_cloud_execution_and_same_form_sells_once(self):
        request = self.request()
        def cloud():
            self.assertFalse(connections["offline"].in_atomic_block)
            command = LocalCommand.objects.using("offline").get()
            self.assertEqual(command.state, "intent")
            self.assertFalse(Venta.objects.exists())
            from mainApp.services.pos_request_receipts import protect_sale
            original = protect_sale(GenerarVentaView.post.__wrapped__)
            with override_settings(NOVA_INTEGRATED_JOURNAL_ENABLED=False):
                return original(GenerarVentaView(), request)
        response = protect_original_request(request, cloud)
        first = json.loads(response.content)
        self.assertTrue(first["success"], first)
        replay = self.sale(request)
        self.assertEqual(json.loads(replay.content)["venta_id"], first["venta_id"])
        self.assertEqual(Venta.objects.count(), 1)
        command = LocalCommand.objects.using("offline").get()
        self.assertEqual(command.state, "accepted")
        self.assertEqual(command.local_result["outcome"], "confirmed")
        self.assertNotIn("print_token", command.local_result["response"])
        self.assertEqual(Inventario.objects.get(sucursalid=self.branch).cantidad, -52)
        self.assertEqual(PuntosPago.objects.get(pk=self.point.pk).dinerocaja, 16345)

    def test_unavailable_local_journal_never_calls_cloud(self):
        with patch("local_pos.integrated_journal.prepare_intent", side_effect=DatabaseError("fixture")), \
                patch("mainApp.views.GenerarVentaView._crear_venta_ultra_fast") as create:
            self.assertEqual(self.sale(self.request()).status_code, 503)
            create.assert_not_called()
        self.assertFalse(Venta.objects.exists())

    def test_missing_local_ack_returns_cloud_success_and_recovers_without_repeating_writes(self):
        request = self.request()
        with patch("local_pos.integrated_journal.record_result", side_effect=DatabaseError("lost local ack")):
            response = self.sale(request)
        self.assertTrue(json.loads(response.content)["success"])
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "intent")
        self.assertEqual(reconcile_pending(), 1)
        self.assertEqual(reconcile_pending(), 0)
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(LocalCommand.objects.using("offline").get().local_result["outcome"], "confirmed")

    def test_commit_then_lost_response_is_recovered_with_same_invoice(self):
        request = self.request()
        from mainApp.services.pos_request_receipts import protect_sale
        original = protect_sale(GenerarVentaView.post.__wrapped__)
        def lost_response():
            with override_settings(NOVA_INTEGRATED_JOURNAL_ENABLED=False):
                response = original(GenerarVentaView(), request)
            self.assertTrue(json.loads(response.content)["success"])
            raise DatabaseError("fixture: transport disconnected after commit")
        response = protect_original_request(request, lost_response)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "pending")
        self.assertEqual(reconcile_pending(), 1)
        self.assertTrue(json.loads(self.sale(request).content)["success"])
        self.assertEqual(Venta.objects.count(), 1)

    def test_loss_before_cloud_commit_does_not_mean_absent_until_receipt_check(self):
        request = self.request()
        with patch("mainApp.views.GenerarVentaView._crear_venta_ultra_fast", side_effect=DatabaseError("fixture disconnect")):
            response = self.sale(request)
        self.assertEqual(response.status_code, 503)
        command = LocalCommand.objects.using("offline").get()
        self.assertEqual(command.state, "pending")
        self.assertFalse(SolicitudPOS.objects.exists())
        with patch("local_pos.integrated_journal.read_cloud_confirmation", side_effect=DatabaseError("still offline")):
            with self.assertRaises(DatabaseError):
                reconcile_pending()
        command.refresh_from_db(using="offline")
        self.assertEqual(command.state, "pending")
        self.assertEqual(reconcile_pending(), 1)
        command.refresh_from_db(using="offline")
        self.assertEqual(command.local_result["outcome"], "absent")
        self.assertFalse(Venta.objects.exists(), "reconciliation must never recreate a sale")
        self.assertTrue(json.loads(self.sale(request).content)["success"])

    def test_crash_after_local_intent_before_cloud_is_resolved_as_absent(self):
        request = self.request()
        from uuid import UUID
        prepare_intent(self.user, UUID(request.headers["X-Nova-Operation-ID"]), request_fingerprint(request))
        self.assertEqual(reconcile_pending(), 1)
        self.assertEqual(LocalCommand.objects.using("offline").get().local_result["outcome"], "absent")
        self.assertFalse(Venta.objects.exists())

    def test_validation_can_be_corrected_but_confirmed_or_uncertain_cart_cannot_change(self):
        operation_id = uuid4()
        response = self.sale(self.request(operation_id, productos="[]", cantidades="[]"))
        self.assertFalse(json.loads(response.content)["success"])
        self.assertEqual(LocalCommand.objects.using("offline").get().local_result["outcome"], "rejected")
        self.assertTrue(json.loads(self.sale(self.request(operation_id)).content)["success"])
        self.assertEqual(self.sale(self.request(operation_id, cantidades="[1]")).status_code, 409)
        self.assertEqual(Venta.objects.count(), 1)

    def test_uncertain_cart_is_not_changed_or_resubmitted_with_different_fields(self):
        request = self.request()
        with patch("mainApp.views.GenerarVentaView._crear_venta_ultra_fast", side_effect=DatabaseError("fixture")):
            self.sale(request)
        operation_id = request.headers["X-Nova-Operation-ID"]
        self.assertEqual(self.sale(self.request(operation_id, cantidades="[1]")).status_code, 409)
        self.assertFalse(Venta.objects.exists())

    def test_secrets_and_raw_post_are_never_stored(self):
        request = self.request(empleado_password="private-employee-password", codigo_descuento_merk2888="private-code")
        self.sale(request)
        command = LocalCommand.objects.using("offline").get()
        stored = json.dumps({"payload": command.payload, "result": command.local_result, "hash": command.fingerprint})
        self.assertNotIn("private-employee-password", stored)
        self.assertNotIn("private-code", stored)
        self.assertNotIn("csrf", stored)
        self.assertEqual(set(command.payload), {"source"})

    def test_recovery_checks_request_hash_and_wrong_cloud_before_reporting_success(self):
        with patch("local_pos.integrated_journal.record_result", side_effect=DatabaseError("fixture")):
            self.sale(self.request())
        command = LocalCommand.objects.using("offline").get()
        SolicitudPOS.objects.update(huella="0" * 64)
        reconcile_pending()
        command.refresh_from_db(using="offline")
        self.assertEqual(command.state, "conflict")
        self.assertNotIn("response", command.local_result)
        with patch("local_pos.integrated_journal.cloud_fingerprint", return_value="other-cloud"):
            with self.assertRaises(PermissionError):
                read_cloud_confirmation(command)

    def test_revoked_user_requires_review_and_does_not_recreate_sale(self):
        with patch("local_pos.integrated_journal.record_result", side_effect=DatabaseError("fixture")):
            self.sale(self.request())
        Usuario.objects.filter(pk=self.user.pk).update(is_active=False)
        reconcile_pending()
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "conflict")
        self.assertEqual(Venta.objects.count(), 1)

    def test_recovery_and_duplicate_click_cannot_race_an_active_submission(self):
        request = self.request()
        from uuid import UUID
        operation_id = UUID(request.headers["X-Nova-Operation-ID"])
        entered, release = Event(), Event()
        def hold():
            try:
                with operation_lock(operation_id) as locked:
                    self.assertTrue(locked)
                    prepare_intent(self.user, operation_id, request_fingerprint(request))
                    entered.set()
                    release.wait(10)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold)
            try:
                self.assertTrue(entered.wait(10))
                self.assertEqual(self.sale(request).status_code, 409)
                self.assertEqual(reconcile_pending(), 0)
            finally:
                release.set()
            future.result(timeout=10)
        self.assertEqual(reconcile_pending(), 1)

    def test_worker_queries_are_read_only_even_if_receipt_code_attempts_write(self):
        request = self.request()
        from uuid import UUID
        command = prepare_intent(self.user, UUID(request.headers["X-Nova-Operation-ID"]), request_fingerprint(request))
        def unsafe(*args, **kwargs):
            PuntosPago.objects.update(dinerocaja=0)
        with patch("local_pos.integrated_journal.recover_receipt", side_effect=unsafe):
            with self.assertRaises(DatabaseError):
                read_cloud_confirmation(command)
        self.assertEqual(PuntosPago.objects.get(pk=self.point.pk).dinerocaja, 12345)

    def test_missing_replica_or_foreign_owner_never_sends_sale(self):
        ReplicaState.objects.using("offline").update(active_id=None)
        self.assertEqual(self.sale(self.request()).status_code, 409)
        self.assertFalse(Venta.objects.exists())

    def test_no_uuid_and_offline_without_authorized_turn_are_not_written(self):
        request = self.request()
        request.META.pop("HTTP_X_NOVA_OPERATION_ID")
        self.assertEqual(self.sale(request).status_code, 409)
        with database_context("offline"):
            self.assertEqual(self.sale(self.request()).status_code, 409)
        self.assertFalse(Venta.objects.using("offline").exists())
        self.assertFalse(Venta.objects.exists())

    @override_settings(NOVA_INTEGRATED_JOURNAL_ENABLED=False)
    def test_disabled_journal_preserves_existing_cloud_sale(self):
        self.assertTrue(json.loads(self.sale(self.request()).content)["success"])
        self.assertFalse(LocalCommand.objects.using("offline").exists())

    def test_confirmation_is_private_to_owner_and_disabled_in_normal_profile(self):
        request = self.request()
        response = self.sale(request)
        operation_id = LocalCommand.objects.using("offline").get().pk
        status_request = RequestFactory().get(f"/local/ventas/confirmacion/{operation_id}/")
        status_request.user = self.user
        result = confirmation(status_request, operation_id)
        self.assertEqual(json.loads(result.content)["response"]["venta_id"], json.loads(response.content)["venta_id"])
        self.assertNotIn(b"print_token", result.content)
        self.assertNotIn(b"fingerprint", result.content)
        with override_settings(NOVA_INTEGRATED_JOURNAL_ENABLED=False):
            with self.assertRaises(Http404):
                confirmation(status_request, operation_id)
        status_request.user = Usuario.objects.create_user("other-journal-user", "fixture-only", rolid=self.user.rolid)
        with self.assertRaises(Http404):
            confirmation(status_request, operation_id)

    def test_confirmation_route_works_while_pending_gate_and_cloud_outage_are_active(self):
        from .connectivity import Health
        request = self.request()
        with patch("local_pos.integrated_journal.record_result", side_effect=DatabaseError("fixture missing ack")):
            self.sale(request)
        command = LocalCommand.objects.using("offline").get()
        self.client.force_login(self.user)
        with patch("local_pos.integrated_middleware.monitor.snapshot", return_value=Health.DISCONNECTED):
            result = self.client.get(f"/local/ventas/confirmacion/{command.pk}/")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["status"], "pending")
        reconcile_pending()
        with patch("local_pos.integrated_middleware.monitor.snapshot", return_value=Health.DISCONNECTED):
            result = self.client.get(f"/local/ventas/confirmacion/{command.pk}/")
        self.assertEqual(result.json()["status"], "confirmed")
