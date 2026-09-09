import importlib
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from uuid import uuid4

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import RequestFactory, TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Categoria, ConteoCierrePTM, DetalleVenta, Inventario, OperacionPTM,
                     Producto, PuntosPago, Sucursal, TurnoCaja, Usuario, Venta)
from .ptm_views import OperacionesPTMView
from .services.ptm import registrar_operacion_ptm, resumen_ptm
from .views import GenerarVentaView, TurnoCajaCerrarApi, TurnoCajaAdminUpdateAPI, _expected_por_metodo


class PTMTests(TestCase):
    def setUp(self):
        self.actor = Usuario.objects.create_user("Cajero PTM")
        self.other = Usuario.objects.create_user("Otro cajero PTM")
        self.branch = Sucursal.objects.create(nombre="Sucursal PTM")
        self.point = PuntosPago.objects.create(nombre="Caja PTM", sucursalid=self.branch)
        self.turn = TurnoCaja.objects.create(cajero=self.actor, puntopago=self.point,
                                            saldo_apertura_efectivo=Decimal("10000"))
        cat = Categoria.objects.create(nombre="Servicios PTM")
        for kind in ("retiro", "recarga"):
            Producto.objects.create(nombre=f"PTM {kind}", tipo_ptm=kind, precio=0, categoria=cat)

    def register(self, **kwargs):
        values = dict(actor=self.actor, turno_id=self.turn.pk, tipo="recarga", monto="5000",
                      referencia="PTM-123456", solicitud_id=uuid4())
        values.update(kwargs)
        return registrar_operacion_ptm(**values)

    def test_cash_directions_are_not_sales_or_stock(self):
        self.register()
        self.register(tipo="retiro", monto="3000", referencia="PTM-7890")
        summary = resumen_ptm(self.turn)
        self.assertEqual(summary["cantidad"], 2)
        self.assertEqual(summary["neto"], Decimal("2000"))
        self.assertFalse(Venta.objects.exists())
        self.assertFalse(DetalleVenta.objects.exists())
        self.assertFalse(Inventario.objects.exists())
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("2000"))

    def test_duplicate_submission_is_idempotent_even_after_closing(self):
        key = uuid4()
        first, created = self.register(solicitud_id=key)
        self.turn.estado = "CERRADO"
        self.turn.save()
        second, created = self.register(solicitud_id=key)
        self.assertFalse(created)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(OperacionPTM.objects.count(), 1)
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("5000"))

    def test_idempotency_key_cannot_change_amount(self):
        key = uuid4()
        self.register(solicitud_id=key)
        with self.assertRaises(ValidationError):
            self.register(solicitud_id=key, monto="6000")

    def test_reference_is_normalized_and_duplicate_does_not_break_transaction(self):
        self.register(referencia="  ptm-123456  ")
        with self.assertRaises(ValidationError):
            self.register()
        self.assertEqual(OperacionPTM.objects.count(), 1)
        self.register(referencia="PTM-999")

    def test_duplicate_reference_across_cashiers_is_blocked(self):
        self.register()
        other_turn = TurnoCaja.objects.create(cajero=self.other, puntopago=self.point)
        with self.assertRaises(ValidationError):
            self.register(actor=self.other, turno_id=other_turn.pk)

    def test_only_current_cashier_can_register(self):
        with self.assertRaises(ValidationError):
            self.register(actor=self.other)
        self.assertFalse(OperacionPTM.objects.exists())

    def test_no_writes_during_or_after_close(self):
        for state in ("CIERRE", "CERRADO"):
            self.turn.estado = state
            self.turn.save()
            with self.assertRaises(ValidationError):
                self.register()

    def test_invalid_values_are_rejected(self):
        for amount in ("0", "-1", "NaN", "Infinity", "0.001", "10000000000", "abc"):
            with self.subTest(amount=amount), self.assertRaises(ValidationError):
                self.register(monto=amount)
        for reference in ("", "x", "<script>test</script>"):
            with self.subTest(reference=reference), self.assertRaises(ValidationError):
                self.register(referencia=reference)
        with self.assertRaises(ValidationError):
            self.register(tipo="nequi")

    def test_saved_operations_are_not_editable_or_deletable(self):
        op, _ = self.register()
        op.monto = Decimal("1")
        with self.assertRaises(ValidationError):
            op.save()
        with self.assertRaises(ValidationError):
            op.delete()
        with self.assertRaises(ProtectedError):
            self.turn.delete()
        with self.assertRaises(ProtectedError):
            op.producto.delete()

    def test_database_rejects_zero_amount_even_if_service_is_bypassed(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            OperacionPTM.objects.create(turno=self.turn, usuario=self.actor,
                producto=Producto.objects.get(tipo_ptm="retiro"), tipo="retiro", monto=0, referencia="ABC")

    def expected(self):
        self.turn.cierre_iniciado = timezone.now()
        self.turn.save()
        with (patch("mainApp.views._sum_pagos_por_metodo", return_value={"efectivo": Decimal("1000")}),
              patch("mainApp.views._sum_ventas_por_mediopago_fallback", return_value={}),
              patch("mainApp.views._sum_reintegros_por_metodo", return_value={}),
              patch("mainApp.views._turn_payment_method_codes", return_value=["efectivo"])):
            return _expected_por_metodo(self.turn)

    def test_expected_cash_adds_deposits_exactly_once(self):
        self.register()
        self.assertEqual(self.expected()[0]["efectivo"], Decimal("6000"))
        self.assertEqual(self.expected()[0]["efectivo"], Decimal("6000"))

    def test_expected_cash_can_be_negative_for_withdrawal_using_base(self):
        self.register(tipo="retiro")
        self.assertEqual(self.expected()[0]["efectivo"], Decimal("-4000"))

    def close(self, count, cash="15000"):
        self.turn.estado = "CIERRE"
        self.turn.cierre_iniciado = timezone.now()
        self.turn.save()
        payload = {"turno_id": self.turn.pk, "efectivo_entregado": cash, "medios_json": "[]"}
        if count is not None:
            payload["ptm_transacciones"] = count
        request = RequestFactory().post(reverse("turno_caja_cerrar"), payload)
        request.user = self.actor
        with (patch("mainApp.views._sum_pagos_por_metodo", return_value={}),
              patch("mainApp.views._sum_ventas_por_mediopago_fallback", return_value={}),
              patch("mainApp.views._sum_reintegros_por_metodo", return_value={}),
              patch("mainApp.views._auto_confirmados_por_metodo", return_value={}),
              patch("mainApp.views._manuales_sin_api_por_metodo", return_value={}),
              patch("mainApp.views._turn_payment_method_codes", return_value=["efectivo"])):
            return TurnoCajaCerrarApi.as_view()(request)

    def test_mismatch_blocks_close_and_saves_attempt(self):
        self.register()
        response = self.close("2")
        self.assertEqual(response.status_code, 400, response.content)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "CIERRE")
        audit = ConteoCierrePTM.objects.get()
        self.assertEqual((audit.declarado, audit.registrado, audit.usuario_id), (2, 1, self.actor.pk))

    def test_invalid_or_missing_count_cannot_close_ptm_turn(self):
        self.register()
        for value in (None, "-1", "1.5", "NaN", "999999999"):
            self.assertEqual(self.close(value).status_code, 400)

    def test_matching_count_and_cash_close_deposit_without_difference(self):
        self.register()
        response = self.close("1")
        self.assertEqual(response.status_code, 200, response.content)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "CERRADO")
        self.assertEqual(self.turn.diferencia_total, 0)

    def test_withdrawal_closes_with_less_physical_cash(self):
        self.register(tipo="retiro")
        response = self.close("1", cash="5000")
        self.assertEqual(response.status_code, 200, response.content)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.diferencia_total, 0)
        self.assertEqual(self.turn.medios.get(metodo="efectivo").esperado, -5000)

    def test_count_does_not_hide_a_cash_shortage(self):
        self.register()
        self.assertEqual(self.close("1", cash="14000").status_code, 200)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.deuda_total, -1000)

    def test_admin_cannot_bypass_ptm_controls(self):
        self.register()
        request = RequestFactory().post("/", data=json.dumps({"estado": "CERRADO"}), content_type="application/json")
        request.user = self.actor
        with patch("mainApp.views._can_edit_turnos", return_value=True):
            response = TurnoCajaAdminUpdateAPI.as_view()(request, turno_id=self.turn.pk)
        self.assertEqual(response.status_code, 409)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "ABIERTO")

    def test_page_enforces_permission(self):
        request = RequestFactory().get(reverse("operaciones_ptm"))
        request.user = self.actor
        with patch("mainApp.ptm_views.user_can_access_url_name", return_value=False):
            self.assertEqual(OperacionesPTMView.as_view()(request).status_code, 403)

    def test_cashier_cannot_read_other_cashier_history(self):
        self.register()
        request = RequestFactory().get(reverse("operaciones_ptm"), {"turno": self.turn.pk})
        request.user = self.other
        with patch("mainApp.ptm_views.user_can_access_url_name", side_effect=lambda user, name: name == "operaciones_ptm"):
            response = OperacionesPTMView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("PTM-123456", response.content.decode())

    def test_ptm_cannot_be_sold_as_a_zero_price_or_discounted_product(self):
        product = Producto.objects.get(tipo_ptm="recarga")
        request = RequestFactory().post(reverse("generar_venta"), {})
        request.user = self.actor
        with (patch("mainApp.views.is_feature_enabled", return_value=True),
              patch("mainApp.views.GenerarVentaForm") as form):
            form.return_value.is_valid.return_value = True
            form.return_value.cleaned_data = {"productos": [str(product.pk)], "cantidades": [1]}
            response = GenerarVentaView.as_view()(request)
        self.assertEqual(response.status_code, 400)
        self.assertIn("PTM", json.loads(response.content)["error"])
        self.assertFalse(Venta.objects.exists())

    def test_write_and_global_balance_roll_back_together(self):
        with patch("mainApp.services.ptm.PuntosPago.objects.filter") as points:
            points.return_value.update.side_effect = IntegrityError("simulated balance failure")
            with self.assertRaises(IntegrityError):
                self.register()
        self.assertFalse(OperacionPTM.objects.exists())
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, 0)

class PTMSeedTests(TestCase):
    def seed(self):
        migration = importlib.import_module("mainApp.migrations.0040_ptm_cash_operations")
        migration.crear_productos_ptm(apps, SimpleNamespace(connection=SimpleNamespace(alias="default")))

    def test_seed_reuses_ptm_id_and_is_idempotent(self):
        cat = Categoria.objects.create(nombre="Varios")
        original = Producto.objects.create(nombre="PTM", precio=1, categoria=cat)
        self.seed()
        self.seed()
        original.refresh_from_db()
        self.assertEqual(original.nombre, "PTM RECARGA O PAGOS")
        self.assertEqual(original.tipo_ptm, "recarga")
        self.assertEqual(Producto.objects.count(), 2)

    def test_seed_without_existing_product(self):
        self.seed()
        self.assertEqual(set(Producto.objects.values_list("tipo_ptm", flat=True)), {"retiro", "recarga"})

    def test_seed_does_not_create_financial_movements(self):
        self.seed()
        self.assertFalse(OperacionPTM.objects.exists())
        self.assertFalse(ConteoCierrePTM.objects.exists())
        self.assertFalse(Venta.objects.exists())

    def test_seed_does_not_silently_merge_two_existing_ids(self):
        cat = Categoria.objects.create(nombre="Varios")
        Producto.objects.create(nombre="PTM", precio=1, categoria=cat)
        Producto.objects.create(nombre="PTM RECARGA O PAGOS", precio=1, categoria=cat)
        with self.assertRaises(RuntimeError):
            self.seed()


class PTMMigrationConstraintTests(SimpleTestCase):
    def test_flush_constraints_before_schema_editor_creates_deferred_indexes(self):
        migration = importlib.import_module("mainApp.migrations.0040_ptm_cash_operations")
        self.assertTrue(migration.Migration.atomic)
        self.assertIs(migration.Migration.operations[-2].code, migration.crear_productos_ptm)
        self.assertIs(migration.Migration.operations[-1].code, migration.finalizar_validaciones_ptm)
        editor = MagicMock()
        editor.connection.vendor = "postgresql"
        migration.finalizar_validaciones_ptm(None, editor)
        editor.execute.assert_called_once_with("SET CONSTRAINTS ALL IMMEDIATE")

    def test_other_backends_do_not_receive_postgresql_sql(self):
        migration = importlib.import_module("mainApp.migrations.0040_ptm_cash_operations")
        editor = MagicMock()
        editor.connection.vendor = "sqlite"
        migration.finalizar_validaciones_ptm(None, editor)
        editor.execute.assert_not_called()
