"""Activation safety gates, with fictional cloud/local databases only."""
import os
from unittest.mock import patch
from uuid import uuid4

from django.db import DatabaseError
from django.test import SimpleTestCase, TransactionTestCase, override_settings

from .integrated_activation import (ActivationError, backup_environment,
    check_expected_scope, commit_activation, install_snapshot, verify_read_pages)
from .integrated_config import DATABASE
from .models import LocalCommand, ReplicaState
from .replica import ReplicaError
from . import test_integrated_replica


class ActivationGateTests(SimpleTestCase):
    def setUp(self):
        self.expected = dict(user_id=1, point_id=3, branch_id=2, required_turn=True, turn_id=1799)
        self.scope = dict(user_id=1, point_id=3, branch_id=2,
                          offline_sale=dict(required_turn=True, turn_id=1799))

    def test_expected_owner_point_branch_and_turn_match(self):
        check_expected_scope(self.scope, self.expected)

    def test_foreign_scope_rejected(self):
        for key in ("user_id", "point_id", "branch_id"):
            with self.subTest(key=key), self.assertRaises(ActivationError):
                check_expected_scope({**self.scope, key: 999}, self.expected)

    def test_closed_or_changed_turn_rejected(self):
        for turn in (None, 1800):
            with self.subTest(turn=turn), self.assertRaises(ActivationError):
                check_expected_scope({**self.scope, "offline_sale": dict(required_turn=True, turn_id=turn)}, self.expected)
        with self.assertRaises(ActivationError):
            check_expected_scope({**self.scope, "offline_sale": {}}, self.expected)

    def test_backup_uses_only_fixed_local_target_not_ambient_pg_settings(self):
        with patch.dict(os.environ, {"PGSERVICE": "cloud", "PGHOSTADDR": "203.0.113.1", "PGPASSWORD": "cloud-secret"}):
            env = backup_environment(dict(database=DATABASE, user=DATABASE, port=55460, password="local-secret"))
        self.assertEqual(env["PGHOST"], "127.0.0.1")
        self.assertEqual(env["PGPASSWORD"], "local-secret")
        self.assertEqual(env["PGPORT"], "55460")
        self.assertNotIn("PGSERVICE", env)
        self.assertNotIn("PGHOSTADDR", env)
        self.assertIn("default_transaction_read_only=on", env["PGOPTIONS"])

    def test_foreign_backup_database_rejected(self):
        with self.assertRaises(ActivationError):
            backup_environment(dict(database="defaultdb", user=DATABASE))

    def test_config_only_changes_activation(self):
        before = {"activation": "database_only", "password": "unchanged"}
        with patch("local_pos.integrated_activation.load_config", return_value=before), \
                patch("local_pos.integrated_activation.save_json") as save:
            commit_activation("path", "project", before)
        save.assert_called_once_with("path", {**before, "activation": "journal_preview"})
        self.assertEqual(before["activation"], "database_only")

    def test_concurrently_changed_config_never_overwritten(self):
        with patch("local_pos.integrated_activation.load_config", return_value={"changed": True}), \
                patch("local_pos.integrated_activation.save_json") as save:
            with self.assertRaises(ActivationError):
                commit_activation("path", "project", {})
        save.assert_not_called()


@override_settings(NOVA_REFERENCE_ENABLED=True, NOVA_INTEGRATED_JOURNAL_ENABLED=True)
class SnapshotActivationTests(TransactionTestCase):
    databases = {"default", "offline"}

    def _fixture_teardown(self):
        with override_settings(DATABASE_ROUTERS=[]):
            super()._fixture_teardown()

    def setUp(self):
        from mainApp.models import ConfiguracionFuncionalidad, TurnoCaja
        from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE, clear_feature_cache
        test_integrated_replica.IntegratedReplicaTests.setUp(self)
        ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=True)
        clear_feature_cache()
        self.turn = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point)
        self.expected = dict(user_id=self.user.pk, point_id=self.point.pk,
            branch_id=self.branch.pk, required_turn=True, turn_id=self.turn.pk)

    def test_verified_snapshot_contains_turn_and_no_sale_created(self):
        from mainApp.models import Venta, TurnoCaja, Inventario
        result = install_snapshot(self.expected)
        self.assertEqual(result["turn_id"], self.turn.pk)
        self.assertEqual(result["counts"]["product"], 1)
        self.assertTrue(TurnoCaja.objects.using("offline").filter(pk=self.turn.pk, estado="ABIERTO").exists())
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -50)
        self.assertFalse(Venta.objects.exists())
        self.assertFalse(Venta.objects.using("offline").exists())

    @override_settings(ROOT_URLCONF="local_pos.test_integrated_journal")
    def test_reference_page_reports_activated_sales_and_limits(self):
        from django.test import RequestFactory
        from .reference_views import index
        install_snapshot(self.expected)
        request = RequestFactory().get("/local/copia/")
        request.user = self.user
        response = index(request)
        self.assertContains(response, "Trabajo sin conexión en las páginas habituales")
        self.assertContains(response, "hasta 24 horas")
        self.assertNotContains(response, "Esta etapa todavía no habilita ventas offline")

    def test_turn_closed_during_download_does_not_install(self):
        from mainApp.models import TurnoCaja
        TurnoCaja.objects.filter(pk=self.turn.pk).update(estado="CERRADO")
        with self.assertRaises(ActivationError):
            install_snapshot(self.expected)
        self.assertFalse(ReplicaState.objects.using("offline").exists())

    def test_pending_operation_prevents_any_download(self):
        install_snapshot(self.expected)
        LocalCommand.objects.using("offline").create(operation_id=uuid4(), node=self.node,
            sequence=1, actor_id=self.user.pk, kind="sale.create", payload={}, fingerprint="x" * 64)
        with patch("local_pos.integrated_replica.capture") as capture:
            with self.assertRaisesRegex(ReplicaError, "conciliar"):
                install_snapshot(self.expected)
        capture.assert_not_called()

    def test_failed_post_copy_verification_rolls_back_copy_and_identity(self):
        from mainApp.models import Producto
        with patch("local_pos.integrated_auth.authorized_identity", return_value=None):
            with self.assertRaises(ActivationError):
                install_snapshot(self.expected)
        self.assertFalse(ReplicaState.objects.using("offline").exists())
        self.assertFalse(Producto.objects.using("offline").exists())

    def test_partial_database_failure_never_installs(self):
        with patch("local_pos.integrated_replica.apply_identity", side_effect=DatabaseError("fictional failure")):
            with self.assertRaises(DatabaseError):
                install_snapshot(self.expected)
        self.assertFalse(ReplicaState.objects.using("offline").exists())

    @override_settings(ROOT_URLCONF="local_pos.test_integrated_journal")
    def test_original_sale_form_renders_read_only_with_both_databases(self):
        from mainApp.models import Venta
        install_snapshot(self.expected)
        self.assertEqual(verify_read_pages(self.user.pk), ["online", "offline"])
        self.assertFalse(Venta.objects.exists())
        self.assertFalse(Venta.objects.using("offline").exists())
