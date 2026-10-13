import importlib
import json
from decimal import Decimal
from unittest.mock import patch

from django.apps import apps
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import RequestFactory, SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import ConfiguracionFuncionalidad, DesglosePagosCierre, Egreso, TurnoCaja, TurnoCajaMedio, Usuario, Rol, Sucursal
from .services.close_payments import validate
from .services.feature_flags import TURN_REQUIRED_FEATURE, clear_feature_cache
from .test_pos_request_receipts import ReceiptFixture


class ClosePaymentValidationTests(SimpleTestCase):
    def test_exact_decimal_sum_and_normalized_names(self):
        total, rows = validate("0.30", [{"nombre": "  coca-cola  ", "monto": "0.10"}, {"nombre": "Juan", "monto": "0.20"}])
        self.assertEqual(total, Decimal("0.30"))
        self.assertEqual(rows[0], {"nombre": "COCA-COLA", "monto": "0.10"})

    def test_invalid_inputs_and_mismatches_are_rejected(self):
        cases = [
            ("100", None), ("100", []), ("100", "invalid json"), ("100", {}),
            ("100", [{"nombre": " ", "monto": "100"}]),
            ("100", [{"nombre": "X", "monto": "99.99"}]),
            ("100", [{"nombre": "X", "monto": "100.01"}]),
            ("100", [{"nombre": "X", "monto": "100.001"}]),
            ("0", [{"nombre": "X", "monto": "-1"}]),
            ("0", [{"nombre": "X", "monto": "0"}]),
            ("NaN", []), ("Infinity", []), ("-1", []), ("0.001", []), ("1e2", []),
            ("10000000000", []), ("0", [{"nombre": "x" * 161, "monto": "1"}]),
            ("0", [{"nombre": "X", "monto": "1"}] * 101),
        ]
        for total, detail in cases:
            with self.subTest(total=total, detail=detail):
                with self.assertRaises(ValueError):
                    validate(total, detail)

    def test_zero_needs_no_invented_payments(self):
        self.assertEqual(validate("0", None), (Decimal("0.00"), []))


class ClosePaymentPersistenceTests(ReceiptFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.user.is_staff = True
        self.user.save(update_fields=["is_staff"])
        self.client.force_login(self.user)
        ConfiguracionFuncionalidad.objects.filter(clave=TURN_REQUIRED_FEATURE).update(habilitada=True)
        clear_feature_cache()
        self.turn = TurnoCaja.objects.create(puntopago=self.point, cajero=self.user, estado="CIERRE", cierre_iniciado=timezone.now())
        self.rows = [{"nombre": "Proveedor", "monto": "100.25"}, {"nombre": "Compañero", "monto": "50.25"}]

    def close(self, **changes):
        return self.client.post(reverse("turno_caja_cerrar"), {
            "turno_id": self.turn.pk, "efectivo_entregado": "0", "facturas_pagadas": "150.50",
            "pagos_detalle": json.dumps(self.rows), "medios_json": "[]", "ptm_transacciones": "0", **changes,
        })

    def test_saves_detail_and_both_dashboards_show_it_without_duplicate_expense(self):
        response = self.close()
        self.assertEqual(response.status_code, 200, response.content)
        record = DesglosePagosCierre.objects.get(turno=self.turn)
        self.assertEqual(record.total, Decimal("150.50"))
        self.assertEqual(record.registrado_por, self.user)
        self.assertEqual(record.pagos[1]["nombre"], "COMPAÑERO")
        self.assertFalse(Egreso.objects.exists())
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("0"))
        self.assertEqual(TurnoCajaMedio.objects.get(turno=self.turn, metodo="facturas_pagadas").contado, record.total)
        for name in ("api_turno_caja_detail", "api_admin_turno_detail"):
            with self.subTest(route=name):
                data = self.client.get(reverse(name, args=[self.turn.pk]))
                self.assertEqual(data.status_code, 200, data.content)
                self.assertEqual(data.json()["pagos_cierre"]["pagos"], record.pagos)
                self.assertEqual(Decimal(str(data.json()["turno"]["facturas_pagadas"])), record.total)

    def test_mismatch_is_rejected_atomically(self):
        response = self.close(facturas_pagadas="200")
        self.assertEqual(response.status_code, 400)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "CIERRE")
        self.assertFalse(DesglosePagosCierre.objects.exists())
        self.assertFalse(TurnoCajaMedio.objects.filter(turno=self.turn).exists())

    def test_http_cannot_bypass_detail_requirement(self):
        response = self.close(pagos_detalle="[]", _allow_legacy_payment_details="true")
        self.assertEqual(response.status_code, 400)

    def test_detail_write_failure_rolls_back_the_entire_close(self):
        with patch("mainApp.services.close_payments.save", side_effect=RuntimeError("Fictional write failure")):
            with self.assertRaises(RuntimeError):
                self.close()
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "CIERRE")
        self.assertIsNone(self.turn.fin)
        self.assertFalse(DesglosePagosCierre.objects.exists())
        self.assertFalse(TurnoCajaMedio.objects.filter(turno=self.turn).exists())

    def test_zero_is_saved_and_repeated_close_cannot_replace_detail(self):
        self.assertEqual(self.close(facturas_pagadas="0", pagos_detalle="[]").status_code, 200)
        self.assertEqual(self.close().status_code, 409)
        self.assertEqual(DesglosePagosCierre.objects.get().pagos, [])

    def test_other_cashier_cannot_close_or_save_details(self):
        from .views import TurnoCajaCerrarApi
        user = Usuario.objects.create_user("other-close", "test-only", rolid=Rol.objects.create(nombre="Cajero"))
        request = RequestFactory().post(reverse("turno_caja_cerrar"), {"turno_id": self.turn.pk})
        request.user = user
        self.assertEqual(TurnoCajaCerrarApi.as_view()(request).status_code, 403)
        self.assertFalse(DesglosePagosCierre.objects.exists())

    def test_old_turns_show_no_invented_detail(self):
        data = self.client.get(reverse("api_turno_caja_detail", args=[self.turn.pk])).json()
        self.assertEqual(data["pagos_cierre"], {"disponible": False, "pagos": []})


class ClosePaymentMigrationTests(TransactionTestCase):
    def test_local_updater_adds_only_detail_and_is_idempotent(self):
        from scripts.update_integrated_close_payments import apply_table, table_counts
        self.assertEqual(connection.settings_dict["NAME"], "test_nova_hybrid_ci")
        Sucursal.objects.create(nombre="Preserve local reference")
        with connection.schema_editor() as editor:
            editor.delete_model(DesglosePagosCierre)
        before = table_counts(connection)
        apply_table(connection)
        self.assertTrue(DesglosePagosCierre.objects.count() == 0)
        after = table_counts(connection)
        self.assertEqual({name: after[name] for name in before}, before)
        self.assertEqual(set(after) - set(before), {"desglose_pagos_cierre"})
        apply_table(connection)
        self.assertEqual(table_counts(connection), after)

    def test_0050_only_adds_detail_table_and_preserves_existing_rows(self):
        self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
        self.assertEqual(connection.settings_dict["NAME"], "test_nova_hybrid_ci")
        existing = Sucursal.objects.create(nombre="Preserve existing data")
        with override_settings(MIGRATION_MODULES={}):
            state = MigrationLoader(connection).project_state([("mainApp", "0049_solicitudpos")])
        migration = importlib.import_module("mainApp.migrations.0050_desglosepagoscierre").Migration("0050_desglosepagoscierre", "mainApp")
        with connection.schema_editor() as editor:
            editor.delete_model(DesglosePagosCierre)
        with connection.schema_editor() as editor:
            after = migration.apply(state, editor)
        historical = after.apps.get_model("mainApp", "DesglosePagosCierre")
        for field in DesglosePagosCierre._meta.fields:
            self.assertEqual(field.deconstruct()[1:], historical._meta.get_field(field.name).deconstruct()[1:])
        self.assertEqual(Sucursal.objects.get(pk=existing.pk).nombre, existing.nombre)
