import importlib
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from unittest import skipUnless
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.conf import settings
from django.http import Http404
from django.db import transaction, connection, connections
from django.db.migrations.state import ModelState, ProjectState
from django.test import SimpleTestCase, TestCase, TransactionTestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (AuditoriaBolsaPTM, BolsaEfectivoPTM, Categoria, CorteBolsaPTM,
    CuentaLecturaPTM, LecturaDiariaPTM, MovimientoLeidoPTM, OperacionPTM, ParticipacionBolsaPTM,
    Producto, PuntosPago, Rol, Sucursal, TurnoCaja, Usuario)
from .services import ptm_cash_pool as pool
from .services.ptm import registrar_operacion_ptm, resumen_ptm
from .services.ptm_reader import ingest, issue_reader, BOGOTA
from .ptm_cash_pool_views import BolsaPTMTurnoView, BolsaPTMAdminView


class PTMCashPoolTests(TestCase):
    def setUp(self):
        self.admin = Usuario.objects.create_user("Administrador PTM", is_superuser=True,
            rolid=Rol.objects.create(nombre="Web Master"))
        self.branch = Sucursal.objects.create(nombre="Yerbabuena ficticia")
        self.account = CuentaLecturaPTM.objects.create(nombre="Cuenta de prueba", usuario_ptm="DEMO")
        self.reader, _ = issue_reader(account=self.account, name="Prueba", actor=self.admin)
        self.bag = pool.configure(actor=self.admin, branch=self.branch, account=self.account, base="100000")
        self.turns = []
        for name in ("A", "B", "C"):
            user = Usuario.objects.create_user("Cajero " + name)
            point = PuntosPago.objects.create(nombre="Caja " + name, sucursalid=self.branch, dinerocaja=50000)
            self.turns.append(TurnoCaja.objects.create(cajero=user, puntopago=point))
        self.a, self.b, self.c = self.turns
        self.factory = RequestFactory()

    def count(self, turn, value, exit=False):
        if exit:
            turn.estado = "CIERRE"
            turn.cierre_iniciado = timezone.now()
            turn.save(update_fields=["estado", "cierre_iniciado"])
        token = pool.ticket(turn, turn.cajero)
        return pool.count(actor=turn.cajero, turn_id=turn.pk, counted=value, token=token)

    def test_configuration_is_exclusively_web_master_even_for_superusers(self):
        from .permissions import user_can_access_url_name, _resolve_nav_item
        other = Usuario.objects.create_user("Supervisor PTM", is_superuser=True, is_staff=True,
            rolid=Rol.objects.create(nombre="Administrador"))
        self.assertFalse(user_can_access_url_name(other, "bolsa_ptm_admin"))
        self.assertIsNone(_resolve_nav_item({"label": "Bolsas PTM", "url_name": "bolsa_ptm_admin"}, other))
        self.assertTrue(user_can_access_url_name(self.admin, "bolsa_ptm_admin"))
        for action in (None, "crear", "ajuste", "revisar"):
            response = self.request(BolsaPTMAdminView, other,
                None if action is None else {"accion": action})
            self.assertEqual(response.status_code, 403)
        with self.assertRaisesMessage(ValidationError, "Solo Web Master"):
            pool.configure(actor=other, branch=self.branch, account=self.account, base=0)
        with self.assertRaisesMessage(ValidationError, "Solo Web Master"):
            pool.adjust(actor=other, bag=self.bag, amount=100, reason="Prueba denegada", request_id=uuid4())
        with self.assertRaisesMessage(ValidationError, "Solo Web Master"):
            pool.review(actor=other, cut_id=999999, reason="Prueba denegada")

    def move(self, value, when=None, kind="recarga", state="Exitosa"):
        return MovimientoLeidoPTM.objects.create(cuenta=self.account,
            id_tx=str(uuid4()), id_operador="", inicio=when or timezone.now(), producto="NEQUI", valor=value,
            tipo=kind, estado_ptm=state, ultima_lectura=timezone.now(), observado_en=timezone.now())

    def request(self, view, user, data=None, **kwargs):
        req = self.factory.post("/", data or {}) if data is not None else self.factory.get("/")
        req.user = user
        return view.as_view()(req, **kwargs)

    def test_a_balances_then_b_shortage_only_b_responsible(self):
        self.count(self.a, 100000)
        self.count(self.b, 100000)
        self.count(self.a, 100000, exit=True)
        cut = self.count(self.b, 90000, exit=True)
        self.assertEqual(list(cut.participantes.all()), [self.b])
        self.assertEqual(cut.diferencia, Decimal("-10000"))
        self.assertEqual(pool.payload(self.a)["incidencias"], [])
        self.assertEqual(len(pool.payload(self.b)["incidencias"]), 1)

    def test_one_shared_incident_is_not_duplicated_or_inherited(self):
        self.count(self.a, 100000)
        self.count(self.b, 100000)
        first = self.count(self.a, 90000, exit=True)
        second = self.count(self.b, 90000, exit=True)
        third = self.count(self.c, 90000)
        self.assertSetEqual(set(first.participantes.values_list("pk", flat=True)), {self.a.pk, self.b.pk})
        self.assertEqual(second.diferencia, 0)
        self.assertEqual(third.base, 90000)
        self.assertEqual(third.diferencia, 0)
        for turn in (self.a, self.b):
            incidents = pool.payload(turn)["incidencias"]
            self.assertEqual([i["id"] for i in incidents], [first.pk])
            turn.refresh_from_db()
            self.assertEqual(turn.deuda_total, 0)
        self.assertEqual(pool.payload(self.c)["incidencias"], [])
        self.assertEqual(CorteBolsaPTM.objects.filter(diferencia__lt=0).count(), 1)

    def test_surplus_is_hidden_from_cashier_payload_and_page(self):
        self.count(self.a, 100000)
        cut = self.count(self.a, 123456, exit=True)
        private = pool.payload(self.a)
        self.assertEqual(private["incidencias"], [])
        text = json.dumps(private)
        for value in ("esperado", "diferencia", "contado", "base", "23456", "123456"):
            self.assertNotIn(value, text)
        administrative = pool.payload(self.a, admin=True)
        self.assertEqual(administrative["incidencias"][0]["diferencia"], "23456.00")
        self.assertEqual(cut.diferencia, 23456)

    def test_report_is_only_cash_source_errors_and_duplicates_excluded(self):
        self.count(self.a, 100000)
        self.move(20000)
        self.move(-5000, kind="retiro")
        self.move(90000, state="Error")
        self.move(7000, state="Pendiente")
        self.move(8000, kind="otro")
        cut = self.count(self.a, 115000, exit=True)
        self.assertEqual(cut.esperado, 115000)
        self.assertEqual(cut.diferencia, 0)
        self.assertEqual(cut.estado, "provisional")
        self.assertTrue(cut.advertencias)
        self.a.puntopago.refresh_from_db()
        self.assertEqual(self.a.puntopago.dinerocaja, 50000)

    def test_manual_operation_is_separate_and_never_double_counted(self):
        category = Categoria.objects.create(nombre="Pruebas PTM")
        Producto.objects.create(nombre="Recarga prueba", categoria=category, precio=0, tipo_ptm="recarga")
        self.count(self.a, 100000)
        op, _ = registrar_operacion_ptm(actor=self.a.cajero, turno_id=self.a.pk,
            tipo="recarga", monto=20000, referencia="TEST-PTM-123", solicitud_id=uuid4())
        self.assertEqual(op.bolsa, self.bag)
        movement = self.move(20000)
        movement.id_tx = op.referencia
        movement.save(update_fields=["id_tx"])
        cut = self.count(self.a, 120000, exit=True)
        self.assertEqual(cut.esperado, 120000)
        self.assertEqual(resumen_ptm(self.a)["neto_caja"], 0)
        self.a.puntopago.refresh_from_db()
        self.assertEqual(self.a.puntopago.dinerocaja, 50000)

    def test_late_report_revises_original_cut_not_next_baseline(self):
        self.count(self.a, 100000)
        when = timezone.now()
        cut = self.count(self.a, 90000, exit=True)
        next_cut = self.count(self.c, 90000)
        row = {"id_tx": "LATE-PTM-001", "id_operador": "", "inicio": when.astimezone(BOGOTA).strftime("%Y/%m/%d %H:%M:%S"),
            "fin": "", "producto": "NEQUI - RETIROS", "valor": "-10000.00", "estado": "Exitosa", "usuario": "DEMO"}
        # PTM timestamps have seconds precision; put the test interval around that second.
        stamp = when.replace(microsecond=0)
        CorteBolsaPTM.objects.filter(pk=cut.pk).update(desde=stamp - timedelta(seconds=1))
        bag_start = stamp - timedelta(minutes=2)
        BolsaEfectivoPTM.objects.filter(pk=self.bag.pk).update(inicio=bag_start)
        data = {"fecha": stamp.astimezone(BOGOTA).date().isoformat(), "leido_en": timezone.now().isoformat(), "filas": [row]}
        ingest(self.reader, data)
        cut.refresh_from_db()
        next_cut.refresh_from_db()
        self.assertEqual(cut.diferencia, 0)
        self.assertEqual(cut.revision, 2)
        self.assertEqual(next_cut.base, 90000)
        self.assertEqual(next_cut.diferencia, 0)
        count = AuditoriaBolsaPTM.objects.filter(accion="lectura_tardia").count()
        ingest(self.reader, data)
        self.assertEqual(AuditoriaBolsaPTM.objects.filter(accion="lectura_tardia").count(), count)
        self.assertEqual(pool.payload(self.a)["incidencias"], [])

    def test_count_retry_idempotent_and_changed_amount_rejected(self):
        token = pool.ticket(self.a, self.a.cajero)
        first = pool.count(actor=self.a.cajero, turn_id=self.a.pk, counted=100000, token=token)
        second = pool.count(actor=self.a.cajero, turn_id=self.a.pk, counted=100000, token=token)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ParticipacionBolsaPTM.objects.count(), 1)
        with self.assertRaises(ValidationError):
            pool.count(actor=self.a.cajero, turn_id=self.a.pk, counted=90000, token=token)

    def test_stale_parallel_form_rejected(self):
        token = pool.ticket(self.b, self.b.cajero)
        self.count(self.a, 100000)
        with self.assertRaisesMessage(ValidationError, "Otra persona"):
            pool.count(actor=self.b.cajero, turn_id=self.b.pk, counted=100000, token=token)
        self.assertFalse(ParticipacionBolsaPTM.objects.filter(turno=self.b).exists())

    def test_new_movement_during_count_requires_recount(self):
        token = pool.ticket(self.a, self.a.cajero)
        self.move(20000)
        with self.assertRaisesMessage(ValidationError, "durante el conteo"):
            pool.count(actor=self.a.cajero, turn_id=self.a.pk, counted=120000, token=token)

    def test_other_cashier_and_tampered_token_rejected(self):
        with self.assertRaises(ValidationError):
            pool.ticket(self.a, self.b.cajero)
        token = pool.ticket(self.a, self.a.cajero)
        for actor, value in ((self.b.cajero, token), (self.a.cajero, token + "tampered")):
            with self.assertRaises(ValidationError):
                pool.count(actor=actor, turn_id=self.a.pk, counted=100000, token=value)
        with self.assertRaises(Http404):
            self.request(BolsaPTMTurnoView, self.b.cajero, turno_id=self.a.pk)

    def test_adjustment_requires_admin_and_is_idempotent(self):
        values = dict(bag=self.bag, amount=-10000, reason="Retiro administrativo", request_id=uuid4())
        with self.assertRaises(ValidationError):
            pool.adjust(actor=self.a.cajero, **values)
        first = pool.adjust(actor=self.admin, **values)
        self.assertEqual(pool.adjust(actor=self.admin, **values).pk, first.pk)
        self.assertEqual(self.count(self.a, 90000).diferencia, 0)

    def test_one_bag_per_branch_and_no_automatic_activation(self):
        other = Sucursal.objects.create(nombre="Otra sucursal")
        with self.assertRaises(ValidationError):
            pool.configure(actor=self.admin, branch=other, account=self.account, base=10)
        self.assertEqual(BolsaEfectivoPTM.objects.count(), 1)
        with self.assertRaises(ValidationError):
            pool.configure(actor=self.a.cajero, branch=other, account=self.account, base=10)

    def test_cashier_form_never_exposes_expected_and_admin_page_is_restricted(self):
        response = self.request(BolsaPTMTurnoView, self.a.cajero, turno_id=self.a.pk)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"100000", response.content)
        self.assertEqual(response["Cache-Control"], "no-store, private")
        self.assertEqual(self.request(BolsaPTMAdminView, self.a.cajero).status_code, 403)
        response = self.request(BolsaPTMAdminView, self.admin)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_close_requires_delivery_without_adding_normal_debt(self):
        from .views import TurnoCajaCerrarApi, TurnoCajaCerrarAPI, TurnoCajaAdminUpdateAPI
        self.count(self.a, 100000)
        self.a.estado = "CIERRE"
        self.a.save(update_fields=["estado"])
        response = self.request(TurnoCajaCerrarApi, self.a.cajero, {"turno_id": self.a.pk})
        self.assertEqual(response.status_code, 409)
        self.assertIn("bolsa", json.loads(response.content)["error"].lower())
        req = self.factory.post("/", "{}", content_type="application/json")
        req.user = self.a.cajero
        self.assertEqual(TurnoCajaCerrarAPI.as_view()(req, turno_id=self.a.pk).status_code, 409)
        req.user = self.admin
        self.assertEqual(TurnoCajaAdminUpdateAPI.as_view()(req, turno_id=self.a.pk).status_code, 409)
        self.count(self.a, 90000, exit=True)
        pool.assert_delivered(self.a)
        response = self.request(TurnoCajaCerrarApi, self.a.cajero, {"turno_id": self.a.pk,
            "efectivo_entregado": "0", "facturas_pagadas": "0", "pagos_detalle_json": "[]", "medios_json": "[]", "ptm_transacciones": "0"})
        self.assertEqual(response.status_code, 200, response.content)
        result = json.loads(response.content)
        self.assertTrue(result["success"])
        self.assertEqual(result["deuda_total"], 0)
        self.assertEqual(result["bolsa_ptm"]["incidencias"][0]["faltante"], "10000.00")

    @override_settings(HYBRID_LOCAL_ENABLED=True, NOVA_REFERENCE_ENABLED=True)
    @override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "local_pos"])
    def test_offline_open_and_close_are_blocked_for_shared_bag(self):
        from local_pos.integrated_turns import build_open
        from local_pos.integrated_offline_sales import SaleError
        state = SimpleNamespace(scope={"offline_business": {"has_ptm_bag": True}})
        with self.assertRaisesMessage(SaleError, "bolsa PTM"):
            build_open(None, state)
        with patch("local_pos.request_context.offline_request", return_value=True):
            with self.assertRaisesMessage(ValidationError, "offline"):
                pool.ticket(self.a, self.a.cajero)

    def test_money_validation(self):
        for value in ("nan", "Infinity", "-1", "0.001", "abc", "1000000000000"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                pool.money(value)

    def test_migration_is_additive_and_model_state_matches(self):
        from django.apps import apps
        from django.db.migrations.operations.models import CreateModel, AddConstraint
        from django.db.migrations.operations.fields import AddField
        migration = importlib.import_module("mainApp.migrations.0052_ptm_cash_pool").Migration
        self.assertTrue(all(isinstance(op, (CreateModel, AddField, AddConstraint)) for op in migration.operations))
        state = ProjectState.from_apps(apps)
        for op in migration.operations:
            if isinstance(op, CreateModel):
                state.remove_model("mainApp", op.name.lower())
        state.remove_field("mainApp", "operacionptm", "bolsa")
        for op in migration.operations:
            op.state_forwards("mainApp", state)
        for name in ("LecturaDiariaPTM", "BolsaEfectivoPTM", "AjusteBolsaPTM", "CorteBolsaPTM", "AuditoriaBolsaPTM", "ParticipacionBolsaPTM", "OperacionPTM"):
            self.assertEqual(state.models[("mainApp", name.lower())], ModelState.from_model(apps.get_model("mainApp", name)))

    def test_review_requires_complete_report_after_count_not_partial_batch(self):
        self.count(self.a, 100000)
        cut = self.count(self.a, 90000, exit=True)
        with self.assertRaises(ValidationError):
            pool.review(actor=self.admin, cut_id=cut.pk, reason="Comprobantes revisados")
        LecturaDiariaPTM.objects.create(cuenta=self.account, fecha=cut.hasta.astimezone(BOGOTA).date(), completada_en=timezone.now())
        pool.review(actor=self.admin, cut_id=cut.pk, reason="Comprobantes revisados")
        cut.refresh_from_db()
        self.assertEqual(cut.estado, "revisado")
        self.assertEqual(self.a.deuda_total, 0)

    def test_complete_report_ack_is_saved_by_date(self):
        reader, token = issue_reader(account=self.account, name="Cobertura de pruebas", actor=self.admin)
        day = timezone.now().astimezone(BOGOTA).date()
        for report_day in (day - timedelta(days=1), day):
            response = self.client.post(reverse("ptm_reader_receive"), json.dumps({"accion": "estado", "estado": "ok", "fecha": report_day.isoformat()}),
                content_type="application/json", HTTP_AUTHORIZATION="Bearer " + token)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(LecturaDiariaPTM.objects.filter(cuenta=self.account).count(), 2)

    def test_review_requires_coverage_for_every_day_of_interval(self):
        first = timezone.now() - timedelta(days=2)
        end = timezone.now()
        LecturaDiariaPTM.objects.create(cuenta=self.account, fecha=end.astimezone(BOGOTA).date(), completada_en=end)
        self.assertTrue(pool.reading_warnings(self.bag, first, end))

    def test_admin_edit_and_delete_cannot_remove_pool_history(self):
        from .views import TurnoCajaAdminDeleteAPI
        self.count(self.a, 100000)
        response = self.request(TurnoCajaAdminDeleteAPI, self.admin, {}, turno_id=self.a.pk)
        self.assertEqual(response.status_code, 409)
        self.assertTrue(TurnoCaja.objects.filter(pk=self.a.pk).exists())


@skipUnless(connection.vendor == "postgresql", "Real row locks require disposable PostgreSQL")
class PTMPoolConcurrencyTests(TransactionTestCase):
    setUp = PTMCashPoolTests.setUp

    def test_concurrent_receipts_only_one_can_consume_the_bag_version(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        tokens = [pool.ticket(t, t.cajero) for t in (self.a, self.b)]
        def run(pair):
            turn, token = pair
            try:
                barrier.wait(timeout=10)
                pool.count(actor=turn.cajero, turn_id=turn.pk, counted=100000, token=token)
                return "saved"
            except ValidationError:
                return "stale"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(run, zip((self.a, self.b), tokens)))
        self.assertCountEqual(results, ["saved", "stale"])
        self.assertEqual(CorteBolsaPTM.objects.count(), 1)
        self.assertEqual(ParticipacionBolsaPTM.objects.count(), 1)


@skipUnless(connection.vendor == "postgresql", "Migration DDL requires disposable PostgreSQL")
class PTMPoolMigrationTests(TransactionTestCase):
    def test_real_migration_preserves_legacy_operations_and_cash(self):
        from django.db.migrations.loader import MigrationLoader
        self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
        self.assertEqual(connection.settings_dict["NAME"], "test_nova_hybrid_ci")
        user = Usuario.objects.create_user("Legacy fixture")
        branch = Sucursal.objects.create(nombre="Preserve me")
        point = PuntosPago.objects.create(nombre="Legacy point", sucursalid=branch, dinerocaja=12345)
        turn = TurnoCaja.objects.create(cajero=user, puntopago=point)
        category = Categoria.objects.create(nombre="Legacy category")
        product = Producto.objects.create(nombre="Legacy PTM", categoria=category, precio=0, tipo_ptm="recarga")
        operation = OperacionPTM.objects.create(turno=turn, usuario=user, producto=product, tipo="recarga", monto=1000,
            referencia="PRESERVE-REFERENCE", solicitud_id=uuid4())
        with override_settings(MIGRATION_MODULES={}):
            before = MigrationLoader(connection).project_state([("mainApp", "0051_ptm_reader")])
        migration = importlib.import_module("mainApp.migrations.0052_ptm_cash_pool").Migration("0052_ptm_cash_pool", "mainApp")
        with connection.schema_editor() as editor:
            migration.unapply(before, editor)
        with connection.schema_editor() as editor:
            migration.apply(before, editor)
        point.refresh_from_db()
        operation.refresh_from_db()
        self.assertEqual(point.dinerocaja, 12345)
        self.assertIsNone(operation.bolsa_id)
        self.assertEqual(operation.monto, 1000)
        self.assertEqual(operation.referencia, "PRESERVE-REFERENCE")
        self.assertFalse(BolsaEfectivoPTM.objects.exists())

    def test_local_additive_updater_is_idempotent_and_preserves_rows(self):
        from django.db.migrations.loader import MigrationLoader
        from scripts.update_integrated_close_payments import apply_table, table_counts
        self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
        self.assertEqual(connection.settings_dict["NAME"], "test_nova_hybrid_ci")
        Sucursal.objects.create(nombre="Local fixture preserved")
        with override_settings(MIGRATION_MODULES={}):
            before = MigrationLoader(connection).project_state([("mainApp", "0051_ptm_reader")])
        migration = importlib.import_module("mainApp.migrations.0052_ptm_cash_pool").Migration("0052_ptm_cash_pool", "mainApp")
        with connection.schema_editor() as editor:
            migration.unapply(before, editor)
        counts = table_counts(connection)
        apply_table(connection, 'ptm-pool')
        after = table_counts(connection)
        self.assertEqual({name: after[name] for name in counts}, counts)
        self.assertEqual(len(set(after) - set(counts)), 7)  # Six models + participants M2M.
        apply_table(connection, 'ptm-pool')
        self.assertEqual(table_counts(connection), after)


class PTMPoolDeploymentPlanTests(SimpleTestCase):
    def test_only_the_selected_forward_migration_is_accepted(self):
        from unittest.mock import MagicMock
        from scripts.migrate_ptm_reader_with_backup import checked_plan
        migration = importlib.import_module("mainApp.migrations.0052_ptm_cash_pool").Migration("0052_ptm_cash_pool", "mainApp")
        executor = MagicMock()
        with patch("django.db.migrations.executor.MigrationExecutor", return_value=executor):
            executor.migration_plan.return_value = [(migration, False)]
            self.assertEqual(len(checked_plan(None, "ptm-pool")[1]), 1)
            executor.migration_plan.return_value = [(migration, True)]
            with self.assertRaises(RuntimeError):
                checked_plan(None, "ptm-pool")
            executor.migration_plan.return_value = [(migration, False), (migration, False)]
            with self.assertRaises(RuntimeError):
                checked_plan(None, "ptm-pool")
