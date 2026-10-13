"""Fictional databases only: original pages -> durable queue -> cloud receipts."""
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from django.test import TransactionTestCase, RequestFactory, override_settings
from django.db import DatabaseError
from django.utils import timezone
from mainApp.models import (Usuario, Venta, DetalleVenta, PagoVenta, TurnoCaja, MetodoPago,
    Egreso, Inventario, SolicitudPOS, ReintegroVenta, ConfiguracionFuncionalidad)
from mainApp.views import RegistrarEgresoView, VentaDetailView, TurnoCajaIniciarCierreApi, TurnoCajaCerrarApi
from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE, clear_feature_cache
from .test_integrated_replica import IntegratedReplicaTests
from .test_integrated_offline_sales import IntegratedOfflineSaleTests
from .request_context import database_context
from .models import LocalCommand, ReplicaState, IntegratedIdentity
from .integrated_offline_sales import flush
from .integrated_business import upload, KINDS
from .integrated_review import retry


@override_settings(NOVA_REFERENCE_ENABLED=True, NOVA_INTEGRATED_JOURNAL_ENABLED=True,
    ROOT_URLCONF="local_pos.test_integrated_journal", DEBUG=False)
class IntegratedBusinessTests(TransactionTestCase):
    databases = {"default", "offline"}
    sync = IntegratedReplicaTests.sync

    def _fixture_teardown(self):
        with override_settings(DATABASE_ROUTERS=[]):
            super()._fixture_teardown()

    def setUp(self):
        IntegratedReplicaTests.setUp(self)
        from mainApp.models import Permiso, UsuarioPermiso
        permission, _ = Permiso.objects.get_or_create(nombre="ventas_cambios")
        UsuarioPermiso.objects.create(usuario=self.user, permiso=permission, permitido=True)
        ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=True)
        clear_feature_cache()
        self.turn = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point)
        self.sale = Venta.objects.create(fecha=timezone.localdate(), hora=timezone.localtime().time(),
            empleadoid=self.employee, sucursalid=self.branch, puntopagoid=self.point, total=6000, mediopago="efectivo")
        self.detail = DetalleVenta.objects.create(ventaid=self.sale, productoid=self.product, cantidad=3, preciounitario=2000)
        PagoVenta.objects.create(ventaid=self.sale, medio_pago="efectivo", monto=6000)
        self.sync()

    def request(self, data, operation_id=None, offline=True):
        request = RequestFactory().post("/fictional/", data, HTTP_X_NOVA_OPERATION_ID=str(operation_id or uuid4()))
        request.user = Usuario.objects.using("offline" if offline else "default").get(pk=self.user.pk)
        request.session = {}
        from django.contrib.messages.storage.fallback import FallbackStorage
        request._messages = FallbackStorage(request)
        return request

    def expense(self, **changes):
        return self.request({"concepto": "  transporte   prueba ", "monto": "1.000", "medio_pago": "efectivo", **changes})

    def run_view(self, view, request, **kwargs):
        with database_context("offline"):
            return view().post(request, **kwargs)

    def refund_request(self, count=1):
        return self.request({"mediopago": "efectivo", "accion": "guardar",
            "dev-TOTAL_FORMS": "1", "dev-INITIAL_FORMS": "0", "dev-0-detalle_id": str(self.detail.pk), "dev-0-devolver": str(count),
            "reint-TOTAL_FORMS": "1", "reint-INITIAL_FORMS": "0", "reint-0-medio_pago": "efectivo", "reint-0-monto": str(2000 * count)})

    def close_offline(self, turn_id):
        self.assertEqual(self.run_view(TurnoCajaIniciarCierreApi, self.request({"turno_id": turn_id})).status_code, 200)
        response = self.run_view(TurnoCajaCerrarApi, self.request({"turno_id": turn_id,
            "efectivo_entregado": "10000", "facturas_pagadas": "0", "medios_json": "[]"}))
        self.assertEqual(response.status_code, 200, response.content)

    def test_offline_close_preserves_payment_details_and_uploads_once(self):
        from mainApp.models import DesglosePagosCierre
        self.assertEqual(self.run_view(TurnoCajaIniciarCierreApi, self.request({"turno_id": self.turn.pk})).status_code, 200)
        rows = [{"nombre": "Coca-cola", "monto": "1500"}, {"nombre": "Juan", "monto": "500"}]
        bad = self.run_view(TurnoCajaCerrarApi, self.request({"turno_id": self.turn.pk,
            "facturas_pagadas": "2500", "pagos_detalle": json.dumps(rows)}))
        self.assertEqual(bad.status_code, 409, bad.content)
        self.assertEqual(TurnoCaja.objects.using("offline").get(pk=self.turn.pk).estado, "CIERRE")
        response = self.run_view(TurnoCajaCerrarApi, self.request({"turno_id": self.turn.pk,
            "efectivo_entregado": "8000", "facturas_pagadas": "2000", "medios_json": "[]", "pagos_detalle": json.dumps(rows)}))
        self.assertEqual(response.status_code, 200, response.content)
        command = LocalCommand.objects.using("offline").get(kind=KINDS["close"])
        self.assertEqual(command.payload["values"]["bills_details"][0]["nombre"], "COCA-COLA")
        self.assertFalse(DesglosePagosCierre.objects.exists())
        self.assertEqual(flush(), 2)
        self.assertEqual(flush(), 0)
        self.assertEqual(DesglosePagosCierre.objects.get(turno=self.turn).total, Decimal("2000"))
        self.assertEqual(DesglosePagosCierre.objects.count(), 1)

    def open_offline(self, **changes):
        from mainApp.views import TurnoCajaRecuperarOIniciarView
        request = self.request({"puntopago_id": self.point.pk, "usuario_id": self.user.pk,
            "password": "fictional-only", "saldo_apertura_efectivo": "1000", **changes})
        response = self.run_view(TurnoCajaRecuperarOIniciarView, request)
        return request, response

    def test_old_signed_close_without_detail_still_syncs_without_inventing_names(self):
        from mainApp.models import DesglosePagosCierre, TurnoCajaMedio
        from .integrated_offline_sales import seal
        self.close_offline(self.turn.pk)
        command = LocalCommand.objects.using("offline").get(kind=KINDS["close"])
        # Reproduce the shape and signature of a pending event from the old version.
        command.payload["values"].pop("bills_details")
        command.payload["values"]["bills"] = "2000.00"
        command.local_result["seal"] = seal(command.payload)
        command.save(using="offline", update_fields=["payload", "local_result"])
        self.assertEqual(flush(), 2)
        self.assertEqual(flush(), 0)
        self.assertEqual(TurnoCajaMedio.objects.get(turno=self.turn, metodo="facturas_pagadas").contado, Decimal("2000"))
        self.assertFalse(DesglosePagosCierre.objects.exists())

    def test_two_offline_shifts_sale_and_close_map_to_cloud_once(self):
        self.close_offline(self.turn.pk)
        request, response = self.open_offline()
        self.assertEqual(response.status_code, 200, response.content)
        local_id = json.loads(response.content)["turno_id"]
        from mainApp.views import GenerarVentaView, TurnoCajaRecuperarOIniciarView
        self.assertEqual(json.loads(self.run_view(TurnoCajaRecuperarOIniciarView, request).content), json.loads(response.content))
        sale = self.run_view(GenerarVentaView, IntegratedOfflineSaleTests.request(self))
        self.assertEqual(sale.status_code, 200, sale.content)
        self.close_offline(local_id)
        self.assertEqual(flush(), 6)
        self.assertEqual(flush(), 0)
        receipt = SolicitudPOS.objects.get(accion="open")
        self.assertNotEqual(receipt.respuesta["turno_id"], local_id)
        self.assertEqual(TurnoCaja.objects.get(pk=receipt.respuesta["turno_id"]).estado, "CERRADO")
        self.assertEqual(TurnoCaja.objects.count(), 2)
        self.assertEqual(Venta.objects.count(), 2)

    def test_offline_open_rejects_other_box_wrong_password_and_expiry(self):
        self.close_offline(self.turn.pk)
        self.assertEqual(self.open_offline(puntopago_id=self.other_point.pk)[1].status_code, 409)
        for _ in range(5):
            self.assertEqual(self.open_offline(password="bad")[1].status_code, 409)
        self.assertEqual(self.open_offline()[1].status_code, 409)  # lock survives failed transactions
        self.assertEqual(TurnoCaja.objects.using("offline").count(), 1)

    def test_offline_open_cloud_overlap_conflict_stops_later_sale(self):
        self.close_offline(self.turn.pk)
        self.assertEqual(flush(), 2)
        self.assertEqual(self.open_offline()[1].status_code, 200)
        from mainApp.views import GenerarVentaView
        self.run_view(GenerarVentaView, IntegratedOfflineSaleTests.request(self))
        TurnoCaja.objects.create(cajero=self.user, puntopago=self.point)
        self.assertEqual(flush(), 0)
        self.assertEqual(LocalCommand.objects.using("offline").filter(state="conflict").count(), 1)
        self.assertEqual(Venta.objects.count(), 1)

    def test_open_lost_cloud_ack_uses_same_turn_and_expired_grant_denied(self):
        self.close_offline(self.turn.pk)
        self.assertEqual(flush(), 2)
        self.assertEqual(self.open_offline()[1].status_code, 200)
        command = LocalCommand.objects.using("offline").get(kind=KINDS["open"])
        first = upload(command)
        self.assertEqual(flush(), 1)
        self.assertEqual(TurnoCaja.objects.count(), 2)
        self.assertEqual(SolicitudPOS.objects.get(pk=command.pk).respuesta["turno_id"], first["turno_id"])
        ReplicaState.objects.using("offline").update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.open_offline()[1].status_code, 409)

    def prepare_employee_discount(self, web_master=False):
        from mainApp.models import Empleado, Cliente, Rol
        role = self.user.rolid if web_master else Rol.objects.create(nombre="Empleado fixture")
        buyer = Usuario.objects.create_user("buyer-offline", "buyer-password", rolid=role)
        employee = Empleado.objects.create(usuarioid=buyer, sucursalid=self.branch,
            nombre="Buyer", apellido="Fixture", numerodocumento="87123401", telefono="555-BUYER", email="buyer@example.invalid")
        client, _ = Cliente.objects.get_or_create(numerodocumento="87123401", defaults={"nombre": "Buyer", "apellido": "Fixture"})
        self.sync()
        return buyer, employee, client

    def discounted_request(self, client, total, **fields):
        request = IntegratedOfflineSaleTests.request(self, cliente_id=client.pk,
            pagos=json.dumps([{"medio_pago": "efectivo", "monto": str(total)}]) if total else "[]",
            **fields)
        request.META["HTTP_X_NOVA_EXPECTED_TOTAL"] = str(total)
        return request

    def test_employee_discount_hash_only_receipt_and_no_extra_login_grant(self):
        from mainApp.views import GenerarVentaView
        buyer, employee, client = self.prepare_employee_discount()
        _, total, _ = GenerarVentaView._employee_sale_pricing(employee, Decimal(4000))
        request = self.discounted_request(client, total, empleado_password="buyer-password")
        response = self.run_view(GenerarVentaView, request)
        self.assertEqual(response.status_code, 200, response.content)
        command = LocalCommand.objects.using("offline").get()
        self.assertNotIn("buyer-password", json.dumps(command.payload))
        self.assertNotIn(buyer.password, json.dumps(command.payload))
        self.assertFalse(IntegratedIdentity.objects.using("offline").filter(user_id=buyer.pk).exists())
        self.assertEqual(flush(), 1)
        self.assertEqual(Venta.objects.latest("pk").total, total)
        self.assertEqual(SolicitudPOS.objects.get().respuesta["offline_discount"]["authorized_by"], buyer.pk)

    def test_employee_wrong_password_lock_and_webmaster_zero_total(self):
        from mainApp.views import GenerarVentaView
        buyer, employee, client = self.prepare_employee_discount(web_master=True)
        response = self.run_view(GenerarVentaView, self.discounted_request(client, 0, empleado_password="buyer-password"))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(flush(), 1)
        self.assertEqual(Venta.objects.latest("pk").total, 0)
        for _ in range(5):
            self.assertEqual(self.run_view(GenerarVentaView,
                self.discounted_request(client, 0, empleado_password="bad")).status_code, 409)
        self.assertEqual(self.run_view(GenerarVentaView,
            self.discounted_request(client, 0, empleado_password="buyer-password")).status_code, 409)

    def test_employee_changed_password_preserves_conflict_without_second_sale(self):
        from mainApp.views import GenerarVentaView
        buyer, employee, client = self.prepare_employee_discount()
        _, total, _ = GenerarVentaView._employee_sale_pricing(employee, Decimal(4000))
        self.assertEqual(self.run_view(GenerarVentaView,
            self.discounted_request(client, total, empleado_password="buyer-password")).status_code, 200)
        buyer.set_password("changed-after-outage")
        buyer.save(update_fields=["password"])
        self.assertEqual(flush(), 0)
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "conflict")
        self.assertEqual(Venta.objects.count(), 1)

    def test_special_reserved_code_offline_single_use_and_delayed_upload(self):
        from mainApp.models import ClienteEspecial, Cliente, AutorizacionDescuentoEspecial
        from mainApp.services.special_discount import generate_one_time_code, preview_one_time_code, SpecialDiscountError
        from .integrated_discounts import reserve
        from mainApp.views import GenerarVentaView
        client = Cliente.objects.create(nombre="Special fixture", numerodocumento="SPECIAL-ONLY")
        ClienteEspecial.objects.create(cliente=client)
        authorization, code = generate_one_time_code(self.user, uuid4().hex)
        reserve(authorization, self.user)
        with self.assertRaises(SpecialDiscountError): preview_one_time_code(client, code, actor=self.user)
        self.sync()
        request = self.discounted_request(client, 0, codigo_descuento_merk2888=code)
        first = self.run_view(GenerarVentaView, request)
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(json.loads(self.run_view(GenerarVentaView, request).content), json.loads(first.content))
        duplicate = self.run_view(GenerarVentaView, self.discounted_request(client, 0, codigo_descuento_merk2888=code))
        self.assertEqual(duplicate.status_code, 409, duplicate.content)
        self.assertNotIn(code, json.dumps(LocalCommand.objects.using("offline").get().payload))
        # Later synchronization accepts an operation authorized before expiry.
        with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(hours=1)):
            self.assertEqual(flush(), 1)
        authorization.refresh_from_db()
        self.assertIsNotNone(authorization.venta_id)
        self.assertEqual(authorization.venta.total, 0)
        self.assertLess(authorization.usada_en, authorization.expira_en)

    def test_reserved_special_code_revoked_after_outage_becomes_review(self):
        from mainApp.models import ClienteEspecial, Cliente, AutorizacionDescuentoEspecial
        from mainApp.services.special_discount import generate_one_time_code
        from .integrated_discounts import reserve
        from mainApp.views import GenerarVentaView
        client = Cliente.objects.create(nombre="Special fixture", numerodocumento="SPECIAL-ONLY")
        ClienteEspecial.objects.create(cliente=client)
        authorization, code = generate_one_time_code(self.user, uuid4().hex)
        reserve(authorization, self.user)
        self.sync()
        with patch("django.utils.timezone.now", return_value=timezone.now() + timedelta(minutes=16)):
            expired = self.run_view(GenerarVentaView, self.discounted_request(client, 0, codigo_descuento_merk2888=code))
            self.assertEqual(expired.status_code, 409)
        self.assertFalse(LocalCommand.objects.using("offline").exists())
        self.assertEqual(self.run_view(GenerarVentaView,
            self.discounted_request(client, 0, codigo_descuento_merk2888=code)).status_code, 200)
        AutorizacionDescuentoEspecial.objects.filter(pk=authorization.pk).update(revocada_en=timezone.now())
        self.assertEqual(flush(), 0)
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "conflict")
        self.assertEqual(Venta.objects.count(), 1)

    def test_authorization_24h_and_failed_refresh_does_not_extend(self):
        state = ReplicaState.objects.using("offline").get()
        self.assertAlmostEqual((state.expires_at - state.synchronized_at).total_seconds(), 86400, delta=2)
        identity = IntegratedIdentity.objects.using("offline").get()
        self.assertEqual(identity.expires_at - identity.refreshed_at, timedelta(hours=24))
        with patch("local_pos.integrated_replica.capture", side_effect=DatabaseError("fixture outage")):
            with self.assertRaises(DatabaseError): self.sync()
        self.assertEqual(ReplicaState.objects.using("offline").get().expires_at, state.expires_at)

    def test_expense_local_then_cloud_once_preserves_original_date(self):
        request = self.expense()
        first = self.run_view(RegistrarEgresoView, request)
        self.assertEqual(first.status_code, 302, first.content)
        self.assertEqual(self.run_view(RegistrarEgresoView, request).status_code, 302)
        self.assertEqual(Egreso.objects.using("offline").count(), 1)
        self.assertFalse(Egreso.objects.exists())
        self.assertEqual(flush(), 1)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(Egreso.objects.get().concepto.nombre, "TRANSPORTE PRUEBA")
        self.assertEqual(Egreso.objects.get().monto, 1000)
        from django.utils.dateparse import parse_datetime
        self.assertEqual(Egreso.objects.get().creado_en, parse_datetime(LocalCommand.objects.using("offline").get().payload["occurred_at"]))
        self.assertEqual(flush(), 0)

    def test_expense_tax_is_frozen_and_ack_loss_does_not_duplicate(self):
        MetodoPago.objects.create(codigo="tarjeta", nombre="Tarjeta", activo=True, aplica_4xmil_egresos=True)
        self.sync()
        response = self.run_view(RegistrarEgresoView, self.expense(medio_pago="tarjeta", impuesto_esperado="1"))
        self.assertEqual(response.status_code, 302, response.content)
        MetodoPago.objects.filter(pk="tarjeta").update(aplica_4xmil_egresos=False)
        command = LocalCommand.objects.using("offline").get()
        upload(command)
        self.assertEqual(Egreso.objects.get().monto, Decimal("1004"))
        self.assertEqual(flush(), 1)
        self.assertEqual(Egreso.objects.count(), 1)

    def test_nequi_expense_rejected_without_event(self):
        MetodoPago.objects.create(codigo="nequi", nombre="Nequi", activo=True)
        self.sync()
        response = self.run_view(RegistrarEgresoView, self.expense(medio_pago="nequi"))
        self.assertEqual(response.status_code, 409)
        self.assertFalse(LocalCommand.objects.using("offline").exists())

    def test_refund_inventory_and_cloud_receipt_are_atomic_once(self):
        request = self.refund_request()
        response = self.run_view(VentaDetailView, request, venta_id=self.sale.pk)
        self.assertEqual(response.status_code, 302, response.content)
        self.run_view(VentaDetailView, request, venta_id=self.sale.pk)
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -49)
        self.assertEqual(DetalleVenta.objects.using("offline").get().cantidad, 2)
        self.assertEqual(DetalleVenta.objects.get().cantidad, 3)
        self.assertEqual(flush(), 1)
        self.assertEqual(DetalleVenta.objects.get().cantidad, 2)
        self.assertEqual(ReintegroVenta.objects.get().monto, 2000)
        self.assertEqual(flush(), 0)
        self.sync()
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -49)

    def test_concurrent_cloud_refund_conflict_keeps_local_event(self):
        self.run_view(VentaDetailView, self.refund_request(2), venta_id=self.sale.pk)
        DetalleVenta.objects.filter(pk=self.detail.pk).update(cantidad=1)
        self.assertEqual(flush(), 0)
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "conflict")
        self.assertFalse(ReintegroVenta.objects.exists())

    def test_expense_failure_rolls_back_projection_and_event(self):
        with patch.object(Egreso.objects, "create", side_effect=DatabaseError("fixture disk")):
            response = self.run_view(RegistrarEgresoView, self.expense())
        self.assertEqual(response.status_code, 503)
        self.assertFalse(LocalCommand.objects.using("offline").exists())
        self.assertFalse(Egreso.objects.using("offline").exists())

    def test_ordered_sale_expense_start_close_and_final_close(self):
        sale_request = IntegratedOfflineSaleTests.request(self)
        from mainApp.views import GenerarVentaView
        self.assertTrue(json.loads(self.run_view(GenerarVentaView, sale_request).content)["success"])
        self.run_view(RegistrarEgresoView, self.expense())
        start = self.run_view(TurnoCajaIniciarCierreApi, self.request({"turno_id": self.turn.pk}))
        self.assertTrue(json.loads(start.content)["success"], start.content)
        again = self.run_view(GenerarVentaView, IntegratedOfflineSaleTests.request(self))
        self.assertFalse(json.loads(again.content)["success"])
        close_request = self.request({"turno_id": self.turn.pk, "efectivo_entregado": "10000", "facturas_pagadas": "0",
            "medios_json": "[]", "ptm_transacciones": "0"})
        response = self.run_view(TurnoCajaCerrarApi, close_request)
        self.assertTrue(json.loads(response.content)["offline_saved"], response.content)
        self.assertEqual(TurnoCaja.objects.get(pk=self.turn.pk).estado, "ABIERTO")
        self.assertEqual(flush(), 4)
        self.assertEqual(TurnoCaja.objects.get(pk=self.turn.pk).estado, "CERRADO")
        self.assertEqual(TurnoCaja.objects.get(pk=self.turn.pk).diferencia_total, 0)
        self.assertEqual(SolicitudPOS.objects.count(), 4)
        self.assertEqual(flush(), 0)

    def test_review_same_uuid_audited_does_not_skip_first_or_change_amount(self):
        self.run_view(RegistrarEgresoView, self.expense())
        command = LocalCommand.objects.using("offline").get()
        command.state = "conflict"
        command.local_result["reason"] = "fixture method inactive"
        command.save(using="offline")
        token = uuid4()
        retry(self.user, command.pk, token, "Revisé el método de pago")
        retry(self.user, command.pk, token, "Doble clic")
        again = LocalCommand.objects.using("offline").get()
        self.assertEqual(again.state, "pending")
        self.assertEqual(again.payload, command.payload)
        self.assertEqual(len(again.local_result["review_history"]), 1)

    def test_clock_rollback_and_expired_grant_reject_new_expenses(self):
        ReplicaState.objects.using("offline").update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.run_view(RegistrarEgresoView, self.expense()).status_code, 409)
        self.assertFalse(LocalCommand.objects.using("offline").exists())

    def test_online_payment_and_duplicate_post_use_one_cloud_receipt(self):
        request = self.request({"concepto": "PRUEBA ONLINE", "monto": "500", "medio_pago": "efectivo"}, offline=False)
        self.assertEqual(RegistrarEgresoView().post(request).status_code, 302)
        self.assertEqual(RegistrarEgresoView().post(request).status_code, 302)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertFalse(Egreso.objects.using("offline").exists())
        self.assertEqual(SolicitudPOS.objects.count(), 1)

    def test_online_receipt_recovery_does_not_execute_expense_twice(self):
        request = self.request({"concepto": "PRUEBA ACUSE", "monto": "700", "medio_pago": "efectivo"}, offline=False)
        RegistrarEgresoView().post(request)
        command = LocalCommand.objects.using("offline").get()
        LocalCommand.objects.using("offline").filter(pk=command.pk).update(state="intent", local_result={})
        self.assertEqual(flush(), 1)
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "accepted")
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(RegistrarEgresoView().post(request).status_code, 302)
        self.assertEqual(Egreso.objects.count(), 1)

    def test_uncertain_online_payment_cannot_be_replayed_as_offline(self):
        from .integrated_business import ONLINE_KIND
        from .integrated_auth import cloud_fingerprint
        request = self.expense()
        from uuid import UUID
        LocalCommand.objects.using("offline").create(pk=UUID(request.headers["X-Nova-Operation-ID"]),
            node=self.node, actor_id=self.user.pk, sequence=1, kind=ONLINE_KIND,
            payload={"source": cloud_fingerprint(), "action": "expense"}, state="intent")
        response = self.run_view(RegistrarEgresoView, request)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(Egreso.objects.using("offline").exists())

    def test_later_events_do_not_pass_conflict_or_uncertain_sale(self):
        self.run_view(RegistrarEgresoView, self.expense())
        first = LocalCommand.objects.using("offline").get()
        first.state = "conflict"
        first.save(using="offline", update_fields=["state"])
        self.assertEqual(flush(), 0)
        self.assertFalse(Egreso.objects.exists())
        self.assertEqual(self.run_view(RegistrarEgresoView, self.expense()).status_code, 409)

    def test_original_pages_render_offline_and_review_is_accessible_when_blocked(self):
        from .connectivity import Health
        from django.urls import reverse
        from . import integrated_replica
        with patch("local_pos.integrated_middleware.monitor.snapshot", return_value=Health.DISCONNECTED), \
                patch("local_pos.journal_runtime.worker.start"):
            self.client.force_login(Usuario.objects.using("offline").get(pk=self.user.pk))
            for route in (reverse("registrar_egreso"), reverse("ver_venta", args=[self.sale.pk]), reverse("turno_caja"), reverse("integrated_sync")):
                response = self.client.get(route)
                self.assertEqual(response.status_code, 200, (route, response.content[:300]))
            self.run_view(TurnoCajaIniciarCierreApi, self.request({"turno_id": self.turn.pk}))
            for route in ("turno_caja_cierre_pagos", "turno_caja_cierre_efectivo", "turno_caja_cierre_medios"):
                response = self.client.get(reverse(route, args=[self.turn.pk]))
                self.assertEqual(response.status_code, 200, (route, response.content[:300]))
            LocalCommand.objects.using("offline").update(state="conflict")
            self.assertEqual(self.client.get(reverse("integrated_sync")).status_code, 200)

    def test_review_cannot_change_payload_or_force_cloud_closed_turn(self):
        from .integrated_offline_sales import KIND
        from mainApp.views import GenerarVentaView
        self.run_view(GenerarVentaView, IntegratedOfflineSaleTests.request(self))
        TurnoCaja.objects.filter(pk=self.turn.pk).update(estado="CERRADO")
        self.assertEqual(flush(), 0)
        row = LocalCommand.objects.using("offline").get(kind=KIND)
        retry(self.user, row.pk, uuid4(), "Verifiqué el turno en la nube")
        self.assertEqual(flush(), 0)
        again = LocalCommand.objects.using("offline").get(pk=row.pk)
        self.assertEqual(again.state, "conflict")
        self.assertEqual(again.payload, row.payload)
        self.assertIn("turno", again.local_result["reason"].lower())

    def test_domain_validation_failure_becomes_reviewable_conflict_not_endless_retry(self):
        self.run_view(RegistrarEgresoView, self.expense())
        with patch("local_pos.integrated_business.execute_event", side_effect=ValueError("fixture rule changed")):
            self.assertEqual(flush(), 0)
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "conflict")
        self.assertFalse(Egreso.objects.exists())
        self.assertFalse(SolicitudPOS.objects.exists())

    def test_review_offers_safe_ack_link_after_lost_response_but_not_for_conflict(self):
        from .connectivity import Health
        from django.urls import reverse
        self.run_view(RegistrarEgresoView, self.expense())
        command = LocalCommand.objects.using("offline").get()
        with patch("local_pos.integrated_middleware.monitor.snapshot", return_value=Health.DISCONNECTED), \
                patch("local_pos.journal_runtime.worker.start"):
            self.client.force_login(Usuario.objects.using("offline").get(pk=self.user.pk))
            response = self.client.get(reverse("integrated_sync") + "?format=json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["operations"][0]["resume"],
                reverse("registrar_egreso") + "?nova_ack=" + str(command.pk))
            LocalCommand.objects.using("offline").update(state="conflict")
            response = self.client.get(reverse("integrated_sync") + "?format=json")
            self.assertEqual(response.json()["operations"][0]["resume"], "")
