from datetime import date, datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .expense_views import EditarEgresoView
from .models import CambioEgreso, ConceptoEgreso, Egreso, MetodoPago, Permiso, Rol, RolPermiso, Usuario, UsuarioPermiso
from .permissions import clear_permission_cache
from .services.expense_editing import ExpenseEditConflict, edit_operational_expense, expense_edit_token
from .services.operational_expenses import OperationalExpenseError


class ExpenseEditingTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.created_role_permissions = RolPermiso._meta.db_table not in connection.introspection.table_names()
        if cls.created_role_permissions:
            with connection.schema_editor() as editor:
                editor.create_model(RolPermiso)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls.created_role_permissions:
            with connection.schema_editor() as editor:
                editor.delete_model(RolPermiso)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.role = Rol.objects.create(nombre="Cajero")
        self.creator = Usuario.objects.create_user("Cajera original", rolid=self.role)
        self.editor = Usuario.objects.create_user("Web Master prueba", rolid=Rol.objects.create(nombre="Web Master"))
        self.permission = Permiso.objects.create(nombre="Editar pagos registrados")
        for code, name in (("efectivo", "Efectivo"), ("nequi", "Nequi")):
            MetodoPago.objects.create(codigo=code, nombre=name, activo=True, es_efectivo=code == "efectivo")
        self.concept = ConceptoEgreso.objects.create(nombre="AGUA", creado_por=self.creator)
        self.expense = Egreso.objects.create(
            concepto=self.concept, monto=Decimal("1000.00"), medio_pago="efectivo",
            registrado_por=self.creator, registrado_por_nombre=self.creator.nombreusuario,
        )
        Egreso.objects.filter(pk=self.expense.pk).update(creado_en=timezone.now() - timedelta(days=60))
        self.expense.refresh_from_db()
        self.url = reverse("editar_egreso", args=[self.expense.pk])
        self.list_url = reverse("pagos_editar_lista")
        self.client.force_login(self.editor)

    def payload(self, **kwargs):
        data = {
            "concepto": "  energía  local  ", "monto": "1.234.567,89", "medio_pago": "nequi",
            "motivo": "Corrección de digitación", "version": expense_edit_token(self.expense, self.editor),
        }
        data.update(kwargs)
        return data

    def test_web_master_and_navigation_have_access_without_manual_grant(self):
        for url in (self.url, self.list_url, reverse("registrar_egreso")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, self.list_url)
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_unpermitted_user_cannot_get_or_post_even_without_middleware(self):
        self.client.force_login(self.creator)
        for url in (self.url, self.list_url):
            self.assertIn(self.client.get(url).status_code, (302, 403))
            self.assertIn(self.client.post(url, self.payload()).status_code, (302, 403))
        response = self.client.get(reverse("registrar_egreso"))
        self.assertNotContains(response, self.list_url)
        request = RequestFactory().post(self.url, self.payload())
        request.user = self.creator
        with self.assertRaises(PermissionDenied):
            EditarEgresoView.as_view()(request, egreso_id=self.expense.pk)
        with self.assertRaises(PermissionDenied):
            self.service(user=self.creator)
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_individual_permission_grants_access_and_denial_revokes_it(self):
        grant = UsuarioPermiso.objects.create(usuario=self.creator, permiso=self.permission, permitido=True)
        clear_permission_cache(self.creator)
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        grant.permitido = False
        grant.save()
        clear_permission_cache(self.creator)
        self.assertIn(self.client.get(self.url).status_code, (302, 403))

    def test_role_permission_grants_access(self):
        RolPermiso.objects.create(rol=self.role, permiso=self.permission)
        clear_permission_cache(self.creator)
        self.client.force_login(self.creator)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        data = self.payload(version=response.context["form"].initial["version"])
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        self.assertEqual(CambioEgreso.objects.get().usuario, self.creator)

    def test_edit_preserves_creation_changes_only_this_payment_and_records_history(self):
        original_date = self.expense.creado_en
        other = Egreso.objects.create(concepto=self.concept, monto=50, medio_pago="efectivo", registrado_por_nombre="Otro")
        target = ConceptoEgreso.objects.create(nombre="ENERGÍA LOCAL")
        response = self.client.post(self.url, self.payload())
        self.assertRedirects(response, self.url)
        self.expense.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.expense.concepto_id, target.pk)
        self.assertEqual(self.expense.monto, Decimal("1234567.89"))
        self.assertEqual(self.expense.medio_pago, "nequi")
        self.assertEqual(self.expense.creado_en, original_date)
        self.assertEqual(self.expense.registrado_por_id, self.creator.pk)
        self.assertEqual(self.expense.registrado_por_nombre, self.creator.nombreusuario)
        self.assertEqual(other.concepto_id, self.concept.pk)
        self.assertEqual(other.monto, Decimal("50.00"))
        self.assertEqual(ConceptoEgreso.objects.get(pk=self.concept.pk).nombre, "AGUA")
        history = CambioEgreso.objects.get()
        self.assertEqual(history.anterior["monto"], "1000.00")
        self.assertEqual(history.nuevo["monto"], "1234567.89")
        self.assertEqual(history.usuario_nombre, self.editor.nombreusuario)
        self.assertEqual(history.motivo, "Corrección de digitación")
        page = self.client.get(self.url)
        self.assertContains(page, "ENERGÍA LOCAL")
        self.assertContains(page, "Historial de correcciones")

    def test_new_concept_is_uppercase_and_get_does_not_write(self):
        self.client.get(self.url)
        self.assertEqual(ConceptoEgreso.objects.count(), 1)
        self.client.post(self.url, self.payload())
        self.assertTrue(ConceptoEgreso.objects.filter(nombre="ENERGÍA LOCAL", creado_por=self.editor).exists())

    def test_invalid_inputs_and_missing_token_never_change_payment(self):
        for updates in ({"monto": "-2"}, {"monto": "0"}, {"monto": "NaN"}, {"monto": "1.000,999"},
                        {"motivo": " "}, {"version": ""}, {"concepto": ""}, {"medio_pago": "inexistente"}):
            with self.subTest(updates=updates):
                self.assertEqual(self.client.post(self.url, self.payload(**updates)).status_code, 400)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.monto, Decimal("1000.00"))
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_stale_or_replayed_submission_is_rejected(self):
        data = self.payload()
        self.assertEqual(self.client.post(self.url, data).status_code, 302)
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "modificado mientras lo editabas", status_code=409)
        self.assertEqual(CambioEgreso.objects.count(), 1)

    def test_token_cannot_be_forged_or_reused_for_another_user(self):
        for token in ("falso", expense_edit_token(self.expense, self.creator)):
            self.assertEqual(self.client.post(self.url, self.payload(version=token)).status_code, 409)
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_retired_current_method_can_stay_but_other_retired_method_cannot_be_selected(self):
        MetodoPago.objects.filter(pk="efectivo").update(activo=False)
        response = self.client.get(self.url)
        self.assertContains(response, "retirado; puedes conservarlo")
        self.assertEqual(self.client.post(self.url, self.payload(medio_pago="efectivo")).status_code, 302)
        self.expense.refresh_from_db()
        MetodoPago.objects.filter(pk="nequi").update(activo=False)
        self.assertEqual(self.client.post(self.url, self.payload(medio_pago="nequi")).status_code, 400)

    def test_no_change_does_not_create_audit_noise(self):
        response = self.client.post(self.url, self.payload(concepto="agua", monto="1.000", medio_pago="efectivo"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def service(self, **kwargs):
        data = dict(user=self.editor, expense_id=self.expense.pk, concept="NUEVO",
                    amount="12.50", payment_method="nequi", reason="Prueba",
                    version=expense_edit_token(self.expense, self.editor))
        data.update(kwargs)
        return edit_operational_expense(**data)

    def test_history_failure_rolls_back_the_whole_correction(self):
        with patch("mainApp.services.expense_editing.CambioEgreso.objects.create", side_effect=IntegrityError("test")):
            with self.assertRaises(IntegrityError):
                self.service()
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.monto, Decimal("1000.00"))
        self.assertFalse(ConceptoEgreso.objects.filter(nombre="NUEVO").exists())

    def test_search_supports_old_payments_dates_id_author_and_pagination(self):
        date = timezone.localtime(self.expense.creado_en).date().isoformat()
        for params in ({"q": str(self.expense.pk)}, {"q": "AGUA"}, {"q": "Cajera original"},
                       {"desde": date, "hasta": date, "medio": "efectivo"}):
            response = self.client.get(self.list_url, params)
            self.assertEqual(response.context["page_obj"].paginator.count, 1)
            self.assertEqual(response.context["total"], Decimal("1000"))
        self.assertEqual(self.client.get(self.list_url, {"medio": "nequi"}).context["page_obj"].paginator.count, 0)
        for _ in range(31):
            Egreso.objects.create(concepto=self.concept, monto=1, medio_pago="efectivo", registrado_por_nombre="Caja")
        page = self.client.get(self.list_url, {"q": "AGUA", "page": 2})
        self.assertEqual(page.context["page_obj"].paginator.count, 32)
        self.assertContains(page, self.url)

    def test_bad_date_filters_show_error_instead_of_unfiltered_data(self):
        for params in ({"desde": "incorrecto"}, {"desde": "2026-09-12", "hasta": "2026-09-01"}):
            response = self.client.get(self.list_url, params)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.context["page_obj"].paginator.count, 0)

    def test_missing_migration_shows_actionable_message_not_a_server_error(self):
        with patch("mainApp.expense_views.expense_editing_ready", return_value=False):
            for url in (self.url, self.list_url):
                response = self.client.get(url)
                self.assertContains(response, "migración 0041", status_code=503)
            self.assertEqual(self.client.get(reverse("registrar_egreso")).status_code, 200)

    def test_anonymous_and_csrf_protection(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(self.url).status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.editor)
        self.assertEqual(csrf_client.post(self.url, self.payload()).status_code, 403)

    def test_edition_is_reflected_in_original_day_totals(self):
        self.service(amount="400.25")
        date = timezone.localtime(self.expense.creado_en).date().isoformat()
        response = self.client.get(self.list_url, {"desde": date, "hasta": date, "medio": "nequi"})
        self.assertEqual(response.context["total"], Decimal("400.25"))
        self.assertEqual(self.client.get(self.list_url, {"medio": "efectivo"}).context["total"], 0)

    def test_date_input_uses_colombia_day_not_utc_day(self):
        stamp = datetime(2026, 9, 11, 2, 30, 45, 123456, tzinfo=datetime_timezone.utc)
        Egreso.objects.filter(pk=self.expense.pk).update(creado_en=stamp)
        response = self.client.get(self.url)
        self.assertContains(response, 'name="fecha_pago"')
        self.assertContains(response, 'type="date"')
        self.assertEqual(response.context["form"]["fecha_pago"].value(), date(2026, 9, 10))
        self.assertContains(response, 'value="2026-09-10"')

    def test_date_only_correction_keeps_time_author_and_audits_before_after(self):
        stamp = datetime(2026, 9, 11, 2, 30, 45, 123456, tzinfo=datetime_timezone.utc)
        Egreso.objects.filter(pk=self.expense.pk).update(creado_en=stamp)
        self.expense.refresh_from_db()
        response = self.client.post(self.url, self.payload(
            concepto="AGUA", monto="1.000", medio_pago="efectivo", fecha_pago="2026-09-12",
        ))
        self.assertRedirects(response, self.url)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.creado_en, datetime(2026, 9, 13, 2, 30, 45, 123456, tzinfo=datetime_timezone.utc))
        self.assertEqual(self.expense.registrado_por_id, self.creator.pk)
        self.assertEqual(self.expense.registrado_por_nombre, self.creator.nombreusuario)
        self.assertEqual(self.expense.monto, Decimal("1000.00"))
        history = CambioEgreso.objects.get()
        self.assertEqual(history.anterior["fecha_pago"], "2026-09-10")
        self.assertEqual(history.nuevo["fecha_pago"], "2026-09-12")
        self.assertEqual(history.anterior["creado_en"], stamp.isoformat())
        self.assertEqual(history.nuevo["creado_en"], self.expense.creado_en.isoformat())
        self.assertEqual(history.usuario_id, self.editor.pk)
        self.assertLess(abs(timezone.now() - history.creado_en), timedelta(seconds=30))
        page = self.client.get(self.url)
        self.assertContains(page, "Fecha del pago: 10/09/2026")
        self.assertContains(page, "Fecha del pago: 12/09/2026")

    def test_invalid_date_is_rejected_without_changing_other_fields(self):
        for invalid in ("incorrecta", "2026-02-30", "2026-09-10T12:30", "0000-01-01"):
            with self.subTest(value=invalid):
                response = self.client.post(self.url, self.payload(fecha_pago=invalid))
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "fecha de pago válida", status_code=400)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.monto, Decimal("1000.00"))
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_same_or_blank_date_does_not_create_a_correction(self):
        stamp = self.expense.creado_en
        for value in (timezone.localdate(stamp).isoformat(), ""):
            response = self.client.post(self.url, self.payload(
                concepto="AGUA", monto="1.000", medio_pago="efectivo", fecha_pago=value,
            ))
            self.assertEqual(response.status_code, 302)
            self.expense.refresh_from_db()
            self.assertEqual(self.expense.creado_en, stamp)
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_corrected_day_moves_list_and_metrics_totals(self):
        from .views import MetricasNegocioDataView

        previous = timezone.localdate(self.expense.creado_en)
        selected = timezone.localdate() - timedelta(days=1)
        self.service(payment_date=selected, amount="400.25")
        for day, expected in ((previous, Decimal("0")), (selected, Decimal("400.25"))):
            params = {"desde": day.isoformat(), "hasta": day.isoformat()}
            self.assertEqual(self.client.get(self.list_url, params).context["total"], expected)
            request = RequestFactory().get(reverse("metricas_negocio_data"), params)
            request.user = self.editor
            response = MetricasNegocioDataView.as_view()(request)
            self.assertEqual(response.status_code, 200)
            summary = json.loads(response.content)["summary"]
            self.assertEqual(summary["expenses_total"], float(expected))
            self.assertEqual(summary["remaining_total"], -float(expected))

    def test_date_change_invalidates_stale_forms_even_after_reverting_the_date(self):
        original_date = timezone.localdate(self.expense.creado_en)
        stale = self.payload()
        self.service(payment_date=original_date + timedelta(days=1))
        self.expense.refresh_from_db()
        self.service(payment_date=original_date)
        self.assertEqual(self.client.post(self.url, stale).status_code, 409)
        self.assertEqual(CambioEgreso.objects.count(), 2)

    def test_service_rejects_invalid_dates_and_rolls_back_date_on_audit_failure(self):
        stamp = self.expense.creado_en
        for invalid in ("2026-02-30", 123, True, datetime(2026, 1, 1)):
            with self.subTest(value=invalid), self.assertRaises(OperationalExpenseError):
                self.service(payment_date=invalid)
        with patch("mainApp.services.expense_editing.CambioEgreso.objects.create", side_effect=IntegrityError("test")):
            with self.assertRaises(IntegrityError):
                self.service(payment_date=timezone.localdate())
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.creado_en, stamp)
        self.assertEqual(self.expense.monto, Decimal("1000.00"))
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_date_change_is_denied_without_permission(self):
        stamp = self.expense.creado_en
        self.client.force_login(self.creator)
        response = self.client.post(self.url, self.payload(fecha_pago=timezone.localdate().isoformat()))
        self.assertIn(response.status_code, (302, 403))
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.creado_en, stamp)
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_old_history_without_dates_still_renders(self):
        CambioEgreso.objects.create(
            egreso=self.expense, usuario=self.editor, usuario_nombre=self.editor.nombreusuario,
            motivo="Corrección histórica", anterior={"concepto": "AGUA", "monto": "900.00", "medio_nombre": "Efectivo"},
            nuevo={"concepto": "AGUA", "monto": "1000.00", "medio_nombre": "Efectivo"},
        )
        self.assertContains(self.client.get(self.url), "Corrección histórica")

    def test_history_escapes_user_text(self):
        self.service(reason='<script>alert("x")</script>')
        response = self.client.get(self.url)
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "&lt;script&gt;")

    def test_render_browser_fixtures(self):
        self.service(amount="1250000.50", concept="SERVICIO DE ENERGÍA", reason="Corrección del valor de la factura")
        pages = {"expense-edit": self.client.get(self.url), "expense-list": self.client.get(self.list_url)}
        for name, response in pages.items():
            self.assertEqual(response.status_code, 200)
            # Artefactos opcionales de QA: solo contienen los datos ficticios de esta prueba.
            if os.environ.get("POS_TEST_ARTIFACT_DIR"):
                (Path(os.environ["POS_TEST_ARTIFACT_DIR"]) / f"{name}.html").write_text(response.content.decode(), encoding="utf-8")
