import importlib
import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.state import ProjectState, ModelState
from django.test import Client, TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from .models import (AuditoriaLecturaPTM, CuentaLecturaPTM, LectorPTM, MovimientoLeidoPTM,
                    Categoria, Producto, Sucursal, PuntosPago, TurnoCaja, OperacionPTM, Usuario)
from .services.ptm_reader import BOGOTA, authenticate_reader, ingest, issue_reader, link


class PTMReaderTests(TestCase):
    def setUp(self):
        self.actor = Usuario.objects.create_user("Control PTM", is_superuser=True)
        self.cashier = Usuario.objects.create_user("Cajero ficticio")
        self.account = CuentaLecturaPTM.objects.create(nombre="Cuenta ficticia", usuario_ptm="DEMO-ACCOUNT")
        self.reader, self.token = issue_reader(account=self.account, name="PC prueba", actor=self.actor)
        self.branch = Sucursal.objects.create(nombre="Prueba PTM")
        self.point = PuntosPago.objects.create(nombre="Prueba", sucursalid=self.branch, dinerocaja=Decimal("12345"))
        self.turn = TurnoCaja.objects.create(cajero=self.cashier, puntopago=self.point)
        category = Categoria.objects.create(nombre="PTM prueba")
        self.product = Producto.objects.create(nombre="PTM prueba", precio=0, categoria=category, tipo_ptm="recarga")
        self.day = (timezone.now().astimezone(BOGOTA) - timedelta(days=1)).strftime("%Y-%m-%d")
        self.row = {"id_tx": "TX-DEMO-001", "id_operador": "M-DEMO-001", "inicio": self.day.replace("-", "/") + " 12:00:00",
                    "fin": "", "producto": "NEQUI - RECARGAS", "valor": "100000.00", "estado": "Exitosa", "usuario": "DEMO-ACCOUNT"}

    def payload(self, **values):
        return {"fecha": self.day, "leido_en": timezone.now().isoformat(), "filas": [{**self.row, **values}]}

    def operation(self, **values):
        return OperacionPTM.objects.create(turno=self.turn, usuario=self.cashier, producto=self.product,
            **{"tipo": "recarga", "monto": Decimal("100000"), "referencia": "TX-DEMO-001", "solicitud_id": uuid4(), **values})

    def api(self, data=None, token=None):
        return self.client.post(reverse("ptm_reader_receive"), json.dumps(data or self.payload()),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer " + (self.token if token is None else token))

    def test_import_does_not_move_money_or_infer_cashier(self):
        self.assertEqual(ingest(self.reader, self.payload())["nuevas"], 1)
        movement = MovimientoLeidoPTM.objects.get()
        self.assertIsNone(movement.operacion_id)
        self.assertEqual(movement.conciliacion, "sin_vincular")
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("12345"))
        self.assertFalse(OperacionPTM.objects.exists())

    def test_replay_and_multiple_readers_are_idempotent(self):
        second, _ = issue_reader(account=self.account, name="Segundo PC", actor=self.actor)
        for reader in (self.reader, second, self.reader):
            ingest(reader, self.payload())
        self.assertEqual(MovimientoLeidoPTM.objects.count(), 1)
        self.assertEqual(AuditoriaLecturaPTM.objects.count(), 1)

    def test_account_scopes_same_reference(self):
        ingest(self.reader, self.payload())
        account = CuentaLecturaPTM.objects.create(nombre="Otra", usuario_ptm="OTRA")
        second, _ = issue_reader(account=account, name="PC", actor=self.actor)
        ingest(second, self.payload(usuario="OTRA"))
        self.assertEqual(MovimientoLeidoPTM.objects.count(), 2)

    def test_wrong_account_rejected_atomically(self):
        data = self.payload()
        data["filas"].append({**self.row, "id_tx": "TX-DEMO-002", "usuario": "OTRO"})
        with self.assertRaises(ValidationError):
            ingest(self.reader, data)
        self.assertFalse(MovimientoLeidoPTM.objects.exists())

    def test_exact_link_and_changed_status_keeps_cash_untouched(self):
        op = self.operation()
        ingest(self.reader, self.payload())
        movement = MovimientoLeidoPTM.objects.get()
        link(movement_id=movement.pk, operation_id=op.pk, actor=self.actor, reason="Comprobante revisado")
        movement.refresh_from_db()
        self.assertEqual(movement.conciliacion, "vinculado")
        self.assertEqual(movement.operacion.usuario, self.cashier)
        ingest(self.reader, self.payload(estado="Reversada"))
        movement.refresh_from_db()
        self.assertEqual(movement.conciliacion, "diferencia")
        self.assertEqual(movement.operacion_id, op.pk)
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("12345"))
        self.assertEqual(AuditoriaLecturaPTM.objects.count(), 3)

    def test_operator_reference_supported_and_amount_mismatch_visible(self):
        op = self.operation(referencia="M-DEMO-001", monto=999)
        ingest(self.reader, self.payload())
        movement = MovimientoLeidoPTM.objects.get()
        result = link(movement_id=movement.pk, operation_id=op.pk, actor=self.actor, reason="Referencia verificada")
        self.assertEqual(result.conciliacion, "diferencia")

    def test_matching_amount_and_time_never_auto_links(self):
        self.operation()
        ingest(self.reader, self.payload())
        self.assertIsNone(MovimientoLeidoPTM.objects.get().operacion_id)

    def test_wrong_reference_and_missing_reason_block_link(self):
        op = self.operation(referencia="DIFERENTE")
        ingest(self.reader, self.payload())
        m = MovimientoLeidoPTM.objects.get()
        for reason in ("", "Verificado"):
            with self.assertRaises(ValidationError):
                link(movement_id=m.pk, operation_id=op.pk, actor=self.actor, reason=reason)

    def test_one_pos_operation_cannot_cover_two_observations(self):
        op = self.operation()
        ingest(self.reader, self.payload())
        m = MovimientoLeidoPTM.objects.get()
        link(movement_id=m.pk, operation_id=op.pk, actor=self.actor, reason="Verificado")
        ingest(self.reader, self.payload(id_tx="TX-DEMO-002", id_operador=op.referencia))
        second = MovimientoLeidoPTM.objects.get(id_tx="TX-DEMO-002")
        with self.assertRaises(ValidationError):
            link(movement_id=second.pk, operation_id=op.pk, actor=self.actor, reason="Verificado")

    def test_unlink_audited(self):
        op = self.operation()
        ingest(self.reader, self.payload())
        m = MovimientoLeidoPTM.objects.get()
        link(movement_id=m.pk, operation_id=op.pk, actor=self.actor, reason="Verificado")
        link(movement_id=m.pk, operation_id=0, actor=self.actor, reason="Corrección del vínculo", unlink=True)
        m.refresh_from_db()
        self.assertIsNone(m.operacion_id)
        self.assertEqual(AuditoriaLecturaPTM.objects.filter(accion="desvinculado").count(), 1)
        self.assertTrue(OperacionPTM.objects.filter(pk=op.pk).exists())

    def test_negative_withdrawal_and_unknown_product(self):
        ingest(self.reader, self.payload(producto="NEQUI - RETIROS", valor="-50000.00"))
        self.assertEqual(MovimientoLeidoPTM.objects.get().tipo, "retiro")
        ingest(self.reader, self.payload(producto="SERVICIO DESCONOCIDO", valor="-50000.00"))
        self.assertEqual(MovimientoLeidoPTM.objects.get().conciliacion, "revisar")

    def test_no_phone_ip_or_arbitrary_fields_stored(self):
        ingest(self.reader, self.payload(contrato="PRIVATE-DEMO", terminal="PRIVATE-IP", password="PRIVATE-SECRET"))
        self.assertNotIn("PRIVATE", json.dumps(AuditoriaLecturaPTM.objects.get().detalle))

    def test_invalid_amounts_and_rows_rejected(self):
        for value in ("NaN", "Infinity", "10.001", "1,00", "1.000,00", "1e5", "-0", 100):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                ingest(self.reader, self.payload(valor=value))
        for field in ({"inicio": "invalid"}, {"id_tx": "<script>"}, {"usuario": "WRONG"}, {"producto": "x" * 151}):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ingest(self.reader, self.payload(**field))

    def test_large_or_duplicate_batch_rejected(self):
        for rows in ([self.row] * 101, [self.row, self.row]):
            with self.assertRaises(ValidationError):
                ingest(self.reader, {**self.payload(), "filas": rows})

    def test_empty_report_never_deletes_history(self):
        ingest(self.reader, self.payload())
        ingest(self.reader, {**self.payload(), "filas": []})
        self.assertEqual(MovimientoLeidoPTM.objects.count(), 1)

    def test_delayed_batch_does_not_revert_newer_status(self):
        old = {**self.payload(), "leido_en": (timezone.now() - timedelta(minutes=10)).isoformat()}
        ingest(self.reader, old)
        ingest(self.reader, self.payload(estado="Reversada"))
        self.assertEqual(ingest(self.reader, old)["antiguas_ignoradas"], 1)
        self.assertEqual(MovimientoLeidoPTM.objects.get().estado_ptm, "Reversada")

    def test_future_reader_clock_rejected(self):
        with self.assertRaises(ValidationError):
            ingest(self.reader, {**self.payload(), "leido_en": (timezone.now() + timedelta(hours=1)).isoformat()})

    def test_token_scope_expiry_revocation_and_permission(self):
        with patch("mainApp.permissions.user_can_access_url_name", return_value=True):
            self.assertEqual(authenticate_reader(self.token).pk, self.reader.pk)
            self.assertIsNone(authenticate_reader(self.token + "x"))
            self.reader.vence_en = timezone.now() - timedelta(seconds=1)
            self.reader.save()
            self.assertIsNone(authenticate_reader(self.token))
        self.assertNotIn(self.token.split(".")[1], self.reader.token_hash)

    def test_api_accepts_token_not_web_cookie(self):
        with patch("mainApp.permissions.user_can_access_url_name", return_value=True):
            self.assertEqual(self.api().status_code, 200)
        self.assertEqual(self.api(token="bad").status_code, 401)
        self.client.force_login(self.actor)
        self.assertEqual(self.client.post(reverse("ptm_reader_receive"), self.payload(), content_type="application/json").status_code, 401)

    def test_api_failed_db_is_not_acknowledged(self):
        from django.db import OperationalError
        with patch("mainApp.ptm_reader_views.authenticate_reader", side_effect=OperationalError("private")):
            response = self.api()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.content.decode())

    def test_batch_not_marked_complete_until_report_done(self):
        with patch("mainApp.permissions.user_can_access_url_name", return_value=True):
            self.api()
            self.reader.refresh_from_db()
            self.assertIsNone(self.reader.ultima_lectura)
            self.api({"accion": "estado", "estado": "ok", "fecha": self.day})
        self.reader.refresh_from_db()
        self.assertIsNotNone(self.reader.ultima_lectura)

    def test_page_permission_and_render(self):
        self.client.force_login(self.cashier)
        response = self.client.get(reverse("conciliacion_ptm"))
        self.assertIn(response.status_code, (302, 403))
        self.client.force_login(self.actor)
        with patch("mainApp.permissions.user_can_access_url_name", return_value=True), patch("mainApp.ptm_reader_views.user_can_access_url_name", return_value=True):
            response = self.client.get(reverse("conciliacion_ptm"), {"fecha": self.day})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Conciliación PTM")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")

    def test_admin_posts_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        response = client.post(reverse("conciliacion_ptm"), {"accion": "cuenta", "nombre": "X", "usuario_ptm": "Y"})
        self.assertEqual(response.status_code, 403)

    def test_https_account_form_accepts_same_origin_with_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        url = reverse("conciliacion_ptm")
        with patch("mainApp.permissions.user_can_access_url_name", return_value=True), patch("mainApp.ptm_reader_views.user_can_access_url_name", return_value=True):
            page = client.get(url, secure=True)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers["Referrer-Policy"], "same-origin")
            response = client.post(url, {
                "accion": "cuenta", "nombre": "Cuenta HTTPS", "usuario_ptm": "DEMO-HTTPS",
                "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
            }, secure=True, HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(CuentaLecturaPTM.objects.filter(usuario_ptm="DEMO-HTTPS").exists())
        self.assertFalse(OperacionPTM.objects.exists())

    def test_https_account_form_rejects_null_and_external_origins(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        url = reverse("conciliacion_ptm")
        with patch("mainApp.permissions.user_can_access_url_name", return_value=True), patch("mainApp.ptm_reader_views.user_can_access_url_name", return_value=True):
            client.get(url, secure=True)
            for origin in ("null", "https://untrusted.example"):
                with self.subTest(origin=origin):
                    response = client.post(url, {
                        "accion": "cuenta", "nombre": "No crear", "usuario_ptm": "DEMO-BLOCKED",
                        "csrfmiddlewaretoken": client.cookies["csrftoken"].value,
                    }, secure=True, HTTP_ORIGIN=origin)
                    self.assertEqual(response.status_code, 403)
        self.assertFalse(CuentaLecturaPTM.objects.filter(usuario_ptm="DEMO-BLOCKED").exists())

    def test_reader_key_response_keeps_form_and_cache_protections(self):
        self.client.force_login(self.actor)
        with patch("mainApp.permissions.user_can_access_url_name", return_value=True), patch("mainApp.ptm_reader_views.user_can_access_url_name", return_value=True):
            response = self.client.post(reverse("conciliacion_ptm"), {
                "accion": "lector", "cuenta": self.account.pk, "nombre": "Segundo lector de prueba",
            })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")
        self.assertContains(response, "Clave del lector — se muestra una sola vez")


class PTMReaderMigrationTests(SimpleTestCase):
    def test_migration_matches_new_models_without_touching_cash_tables(self):
        module = importlib.import_module("mainApp.migrations.0051_ptm_reader")
        state = ProjectState()
        for op in module.Migration.operations:
            self.assertEqual(type(op).__name__, "CreateModel")
            op.state_forwards("mainApp", state)
        for model in (CuentaLecturaPTM, LectorPTM, MovimientoLeidoPTM, AuditoriaLecturaPTM):
            actual = ModelState.from_model(model)
            migrated = state.models[("mainApp", model._meta.model_name)]
            for name in actual.fields:
                self.assertEqual(actual.fields[name].deconstruct()[1:], migrated.fields[name].deconstruct()[1:], name)
