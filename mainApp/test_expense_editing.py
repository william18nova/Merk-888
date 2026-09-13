from datetime import timedelta
from decimal import Decimal
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
