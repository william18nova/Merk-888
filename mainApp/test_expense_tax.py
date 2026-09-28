import importlib
import json
import os
from pathlib import Path
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.apps import apps
from django.db import connection, IntegrityError, transaction
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import ConceptoEgreso, Egreso, MetodoPago, Rol, TelegramAccionPendiente, TelegramUsuario, Usuario
from .services.expense_editing import edit_operational_expense, expense_edit_token
from .services.expense_tax import expense_amounts, expense_tax_preview
from .services.operational_expenses import OperationalExpenseError, register_operational_expense
from .services.payment_methods import payment_method_options
from . import test_operational_expenses as expense_tests
from .views import MetricasNegocioDataView


class ExpenseTaxArithmeticTests(SimpleTestCase):
    def test_decimal_rounding_and_not_four_percent(self):
        for base, tax, total in (("100000", "400.00", "100400.00"),
                                 ("1234567.89", "4938.27", "1239506.16"),
                                 ("1.25", "0.01", "1.26"), ("0.01", "0.00", "0.01")):
            with self.subTest(base=base):
                self.assertEqual(expense_amounts(base, True), (Decimal(base), Decimal(tax), Decimal(total)))
                self.assertEqual(expense_amounts(base, False), (Decimal(base), Decimal("0"), Decimal(base)))

    def test_invalid_and_overflowing_total_rejected(self):
        for amount in ("NaN", "Infinity", "-1", "0", "0.001", "999999999999.99"):
            with self.subTest(amount=amount), self.assertRaises(OperationalExpenseError):
                expense_amounts(amount, True)


class ExpenseTaxTests(TestCase):
    setUp = expense_tests.OperationalExpenseTests.setUp
    tearDown = expense_tests.OperationalExpenseTests.tearDown

    def enable(self, code="nequi", enabled=True):
        MetodoPago.objects.filter(pk=code).update(aplica_4xmil_egresos=enabled)

    def create(self, method="nequi", amount="100000", **extra):
        return register_operational_expense(user=self.user, concept="agua", amount=amount, payment_method=method, **extra)

    def edit(self, expense, **kwargs):
        args = dict(user=self.user, expense_id=expense.pk, concept=expense.concepto.nombre,
                    amount=expense.monto_base, payment_method=expense.medio_pago, reason="Corregir",
                    version=expense_edit_token(expense, self.user))
        args.update(kwargs)
        with patch("mainApp.services.expense_editing.can_edit_expenses", return_value=True):
            return edit_operational_expense(**args)

    def test_new_web_payment_and_metrics_include_tax_once_without_affecting_cash(self):
        self.enable()
        self.client.force_login(self.user)
        response = self.client.post(reverse("registrar_egreso"), {
            "concepto": "agua", "monto": "100.000", "medio_pago": "nequi", "impuesto_esperado": "1",
            "impuesto_4xmil": "999999", "monto_total": "1",  # Nunca confiar en importes del navegador.
        })
        self.assertEqual(response.status_code, 302)
        expense = Egreso.objects.get()
        self.assertEqual(expense.monto_base, Decimal("100000.00"))
        self.assertEqual(expense.impuesto_4xmil, Decimal("400.00"))
        self.assertEqual(expense.monto, Decimal("100400.00"))
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("5000"))
        response = self.client.get(reverse("registrar_egreso"))
        self.assertEqual(response.context["today_total"], Decimal("100400"))
        self.assertTrue(response.context["expense_tax_rules"]["nequi"])
        self.assertContains(response, "expense-tax-rules")
        request = RequestFactory().get("/metricas/data/", {"desde": timezone.localdate().isoformat(), "hasta": timezone.localdate().isoformat()})
        request.user = self.user
        data = json.loads(MetricasNegocioDataView.as_view()(request).content)
        self.assertEqual(data["summary"]["expenses_total"], 100400)
        self.assertEqual(data["summary"]["expenses_tax_total"], 400)
        self.assertEqual(data["summary"]["remaining_total"], -100400)
        row = next(item for item in data["tables"]["payment_balance"] if item["code"] == "nequi")
        self.assertEqual(row["expenses"], 100400)
        self.assertEqual(data["tables"]["expenses"][0]["monto_base"], 100000)

    def test_toggle_only_affects_future_payments_and_any_method_can_use_it(self):
        old = self.create()
        self.enable()
        taxed = self.create()
        self.enable(enabled=False)
        untaxed = self.create()
        old.refresh_from_db()
        taxed.refresh_from_db()
        self.assertEqual((old.monto, taxed.monto, untaxed.monto), (Decimal("100000"), Decimal("100400"), Decimal("100000")))
        self.enable("efectivo")
        self.assertEqual(self.create("efectivo").impuesto_4xmil, Decimal("400"))
        MetodoPago.objects.create(codigo="transferencia", nombre="Transferencia", aplica_4xmil_egresos=True)
        self.assertEqual(self.create("transferencia").monto, Decimal("100400"))

    def test_stale_preview_and_inactive_method_cannot_record_payment(self):
        self.enable()
        with self.assertRaises(OperationalExpenseError):
            self.create(expected_tax=False)
        MetodoPago.objects.filter(pk="nequi").update(activo=False)
        with self.assertRaises(OperationalExpenseError):
            self.create()
        self.assertFalse(Egreso.objects.exists())
        self.assertFalse(ConceptoEgreso.objects.exists())

    def test_edits_keep_snapshot_do_not_compound_and_audit_tax(self):
        self.enable()
        expense = self.create()
        self.enable(enabled=False)
        edited, changed = self.edit(expense, concept="ENERGIA")
        self.assertTrue(changed)
        self.assertEqual(edited.monto, Decimal("100400"))
        self.assertTrue(expense_tax_preview(payment_method_options(), edited)["nequi"])
        edited, changed = self.edit(edited, amount="200000")
        self.assertEqual(edited.monto, Decimal("200800"))
        self.assertEqual(edited.cambios.first().nuevo["impuesto_4xmil"], "800.00")
        unchanged, changed = self.edit(edited)
        self.assertFalse(changed)
        self.assertEqual(unchanged.monto, Decimal("200800"))
        edited, changed = self.edit(edited, payment_method="efectivo")
        self.assertEqual(edited.monto, Decimal("200000"))
        self.assertEqual(edited.impuesto_4xmil, 0)
        self.assertFalse(edited.aplica_4xmil)

    def test_edit_old_payment_does_not_add_newly_enabled_tax(self):
        expense = self.create()
        self.enable()
        edited, _changed = self.edit(expense, amount="120000")
        self.assertFalse(edited.aplica_4xmil)
        self.assertEqual(edited.monto, Decimal("120000"))

    def test_invalid_tax_rows_rejected_by_database(self):
        expense = self.create()
        for amount in (Decimal("-1"), Decimal("100000"), Decimal("1")):
            with self.subTest(amount=amount), self.assertRaises(IntegrityError), transaction.atomic():
                Egreso.objects.filter(pk=expense.pk).update(impuesto_4xmil=amount)

    def test_configuration_changes_tax_with_password_and_version_protection(self):
        master = Usuario.objects.create_user("admin-4xmil", password="only-test", rolid=Rol.objects.create(nombre="Web Master"))
        self.client.force_login(master)
        url = reverse("configuracion_metodos_pago")
        payload = {"action": "update", "code": "efectivo", "version": "1", "label": "Efectivo", "order": "10",
                   "tax_setting_present": "1", "expense_tax_enabled": "1", "password_web_master": "wrong"}
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MetodoPago.objects.get(pk="efectivo").aplica_4xmil_egresos)
        payload["password_web_master"] = "only-test"
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 302)
        method = MetodoPago.objects.get(pk="efectivo")
        self.assertTrue(method.aplica_4xmil_egresos)
        self.assertEqual(method.version, 2)
        self.assertEqual(method.actualizado_por, master)
        payload.pop("expense_tax_enabled")
        response = self.client.post(url, payload)  # versión vieja
        self.assertEqual(response.status_code, 409)
        payload["version"] = "2"
        self.assertEqual(self.client.post(url, payload).status_code, 302)
        self.assertFalse(MetodoPago.objects.get(pk="efectivo").aplica_4xmil_egresos)

    def test_data_migration_enables_only_nequi_card_preserves_old_expenses(self):
        old = self.create()
        for code in ("tarjeta", "daviplata", "transferencia"):
            MetodoPago.objects.create(codigo=code, nombre=code)
        migration = importlib.import_module("mainApp.migrations.0043_expense_four_per_thousand")
        migration.enable_nequi_and_card(apps, SimpleNamespace(connection=connection))
        enabled = set(MetodoPago.objects.filter(aplica_4xmil_egresos=True).values_list("pk", flat=True))
        self.assertEqual(enabled, {"nequi", "tarjeta"})
        old.refresh_from_db()
        self.assertEqual(old.monto, Decimal("100000"))
        self.assertFalse(old.aplica_4xmil)
        self.assertEqual(self.create("tarjeta").monto, Decimal("100400"))
        self.assertEqual(self.create("Banco Caja Social").monto, Decimal("100400"))

    def test_taxed_form_renders_base_not_total_and_browser_fixtures(self):
        self.enable()
        expense = self.create()
        editor = Usuario.objects.create_user("admin-preview", rolid=Rol.objects.create(nombre="Web Master"))
        self.client.force_login(editor)
        pages = {
            "expense-tax-create": self.client.get(reverse("registrar_egreso")),
            "expense-tax-edit": self.client.get(reverse("editar_egreso", args=[expense.pk])),
            "expense-tax-config": self.client.get(reverse("configuracion_metodos_pago")),
        }
        self.assertEqual(pages["expense-tax-edit"].context["form"].initial["monto"], Decimal("100000"))
        for name, response in pages.items():
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "4 × 1.000")
            if os.environ.get("POS_TEST_ARTIFACT_DIR"):
                (Path(os.environ["POS_TEST_ARTIFACT_DIR"]) / f"{name}.html").write_text(response.content.decode(), encoding="utf-8")

    def test_telegram_proposes_total_and_confirms_once(self):
        from .services.telegram_bot import _handle_callback, tool_prepare_expense
        self.enable()
        profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=94851, telegram_chat_id=94851)
        with patch("mainApp.services.telegram_bot._require_access"):
            reply = tool_prepare_expense(profile, {"concepto": "AGUA", "monto": 100000, "medio_pago": "nequi"})
            self.assertIn("4 × 1.000", reply.text)
            self.assertIn("100.400", reply.text)
            pending = TelegramAccionPendiente.objects.get()
            update = SimpleNamespace(texto=f"confirm:{pending.pk}", callback_query_id="only-test")
            client = SimpleNamespace(answer_callback=MagicMock())
            result = _handle_callback(update, profile, client)
            self.assertIn("4 × 1.000", result.text)
            _handle_callback(update, profile, client)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(Egreso.objects.get().monto, Decimal("100400"))

    def test_telegram_rejects_changed_tax_after_confirmation_was_shown(self):
        from .services.telegram_bot import _handle_callback, tool_prepare_expense
        profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=94852, telegram_chat_id=94852)
        with patch("mainApp.services.telegram_bot._require_access"):
            tool_prepare_expense(profile, {"concepto": "AGUA", "monto": 100000, "medio_pago": "nequi"})
            self.enable()
            pending = TelegramAccionPendiente.objects.get()
            result = _handle_callback(SimpleNamespace(texto=f"confirm:{pending.pk}", callback_query_id="only-test"), profile, SimpleNamespace(answer_callback=MagicMock()))
        self.assertIn("Cambió la configuración", result.text)
        self.assertFalse(Egreso.objects.exists())

    def test_telegram_edit_uses_base_when_only_concept_is_changed(self):
        from .services.telegram_payments import confirm_payment_edit, tool_prepare_payment_edit
        self.enable()
        expense = self.create()
        editor = Usuario.objects.create_user("admin-tax-edit", rolid=Rol.objects.create(nombre="Web Master"))
        profile = TelegramUsuario.objects.create(usuario=editor, telegram_user_id=94853, telegram_chat_id=94853)
        with patch("mainApp.services.telegram_bot._require_access"):
            reply = tool_prepare_payment_edit(profile, {"pago_id": expense.pk, "concepto": "SERVICIOS", "motivo": "Corregir concepto"})
            self.assertIn("100.400", reply.text)
            action = TelegramAccionPendiente.objects.get()
            self.assertEqual(action.argumentos["monto"], "100000.00")
            confirm_payment_edit(profile, action)
        expense.refresh_from_db()
        self.assertEqual(expense.monto, Decimal("100400"))
        self.assertEqual(expense.impuesto_4xmil, Decimal("400"))
