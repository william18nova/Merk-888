"""Two real, isolated PostgreSQL databases; never production data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch, Mock
from uuid import uuid4

from django.conf import settings
from django.core.cache import caches
from django.db import connections, DatabaseError
from django.test import TransactionTestCase, override_settings, RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import path, include
from django.http import HttpResponse
from django.utils import timezone

from mainApp.models import (Rol, Usuario, Empleado, Sucursal, PuntosPago, Producto, Categoria,
    Inventario, Cliente, MetodoPago, UsuarioPermiso, Permiso)
from mainApp.services.hybrid_replica import collect
from .integrated_auth import remember_user, authorized_identity
from .integrated_replica import synchronize, capture, apply_snapshot, ReplicaError, ReplicaScopeError
from .models import LocalNode, ReplicaState, ReplicaRow, ReplicaTransfer, LocalCommand, IntegratedIdentity
from .reference_runtime import ReferenceRefreshMiddleware, ReferenceWorker
from .reference_views import index

urlpatterns = [path("local/copia/", index, name="integrated_reference"), path("", include("mainApp.urls"))]


@override_settings(NOVA_REFERENCE_ENABLED=True, ROOT_URLCONF=__name__)
class IntegratedReplicaTests(TransactionTestCase):
    databases = {"default", "offline"}

    def _fixture_teardown(self):
        with override_settings(DATABASE_ROUTERS=[]):
            super()._fixture_teardown()

    def setUp(self):
        caches["default"].clear()
        caches["offline"].clear()
        self.node = LocalNode.objects.using("offline").create(pk=settings.LOCAL_CONFIG["instance_id"])
        role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("replica-test", "fictional-only", rolid=role)
        self.branch = Sucursal.objects.create(nombre="Reference branch")
        self.other_branch = Sucursal.objects.create(nombre="Other branch")
        self.employee = Empleado.objects.create(usuarioid=self.user, sucursalid=self.branch,
            nombre="Fixture", apellido="Only", numerodocumento="REF-FICTIONAL")
        self.point = PuntosPago.objects.create(nombre="Reference point", sucursalid=self.branch, dinerocaja=12345)
        self.other_point = PuntosPago.objects.create(nombre="Other point", sucursalid=self.other_branch)
        self.category = Categoria.objects.create(nombre="FICTIONAL CATEGORY")
        self.product = Producto.objects.create(nombre="FICTIONAL PRODUCT", precio="2000", categoria=self.category)
        Inventario.objects.create(sucursalid=self.branch, productoid=self.product, cantidad=-50)
        Inventario.objects.create(sucursalid=self.other_branch, productoid=self.product, cantidad=999)
        self.client_row = Cliente.objects.create(nombre="FICTIONAL", apellido="CLIENT", numerodocumento="REF-CLIENT")
        MetodoPago.objects.get_or_create(codigo="efectivo", defaults={"nombre": "Efectivo", "es_efectivo": True})

    def sync(self):
        return synchronize(self.user.pk, self.point.pk)

    def test_first_snapshot_scoped_catalog_identity_permissions_negative_inventory(self):
        state = self.sync()
        self.assertTrue(state.active_id)
        self.assertEqual(state.scope["branch_id"], self.branch.pk)
        self.assertEqual(list(Sucursal.objects.using("offline").values_list("pk", flat=True)), [self.branch.pk])
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -50)
        self.assertEqual(Producto.objects.using("offline").get().precio, 2000)
        self.assertEqual(PuntosPago.objects.using("offline").get().dinerocaja, 0)
        self.assertEqual(Cliente.objects.using("offline").count(), Cliente.objects.count())
        self.assertTrue(MetodoPago.objects.using("offline").filter(pk="efectivo").exists())
        self.assertIsNotNone(authorized_identity(self.user))
        self.assertGreater(state.expires_at, timezone.now())
        # No business write in the source: its money and both branches stay intact.
        self.assertEqual(PuntosPago.objects.get(pk=self.point.pk).dinerocaja, 12345)
        self.assertEqual(Sucursal.objects.count(), 2)
        self.assertEqual(ReplicaState.objects.using("default").count(), 0)

    def test_initial_copy_adopts_only_branch_previously_saved_by_identity(self):
        remember_user(self.user, force=True)
        self.sync()
        self.assertEqual(Producto.objects.using("offline").count(), 1)

    def test_arbitrary_local_data_is_not_overwritten(self):
        Sucursal.objects.using("offline").create(pk=self.branch.pk, nombre=self.branch.nombre)
        with self.assertRaisesRegex(ReplicaError, "datos ajenos"):
            self.sync()
        self.assertFalse(ReplicaState.objects.using("offline").exists())

    def test_delta_updates_prices_inventory_and_payment_methods(self):
        first = self.sync().active_id
        Producto.objects.filter(pk=self.product.pk).update(precio="3000")
        Inventario.objects.filter(sucursalid=self.branch).update(cantidad=-100)
        MetodoPago.objects.filter(pk="efectivo").update(aplica_4xmil_egresos=True)
        state = self.sync()
        self.assertNotEqual(state.active_id, first)
        self.assertEqual(Producto.objects.using("offline").get().precio, 3000)
        self.assertEqual(Inventario.objects.using("offline").get().cantidad, -100)
        self.assertTrue(MetodoPago.objects.using("offline").get(pk="efectivo").aplica_4xmil_egresos)

    def test_unchanged_snapshot_does_not_rewrite_catalog(self):
        self.sync()
        with CaptureQueriesContext(connections["offline"]) as captured:
            self.sync()
        writes = [q["sql"] for q in captured if q["sql"].startswith(("UPDATE \"productos\"", "INSERT INTO \"productos\""))]
        self.assertEqual(writes, [])
        self.assertEqual(ReplicaTransfer.objects.using("offline").count(), 1)

    def test_network_interruption_keeps_previous_copy_and_expiry(self):
        state = self.sync()
        with patch("local_pos.integrated_replica.capture", side_effect=DatabaseError("fictional disconnect")):
            with self.assertRaises(DatabaseError):
                self.sync()
        again = ReplicaState.objects.using("offline").get()
        self.assertEqual((again.active_id, again.manifest, again.expires_at), (state.active_id, state.manifest, state.expires_at))
        self.assertFalse(again.blocked)

    def test_partial_apply_rolls_back_catalog_permissions_and_metadata(self):
        state = self.sync()
        Producto.objects.filter(pk=self.product.pk).update(precio=5000)
        with patch("local_pos.integrated_replica.apply_identity", side_effect=RuntimeError("interrupted activation")):
            with self.assertRaises(RuntimeError):
                self.sync()
        self.assertEqual(Producto.objects.using("offline").get().precio, 2000)
        self.assertEqual(ReplicaState.objects.using("offline").get().active_id, state.active_id)
        self.assertEqual(ReplicaTransfer.objects.using("offline").count(), 1)

    def test_local_edits_and_pending_movements_are_never_overwritten(self):
        self.sync()
        Producto.objects.using("offline").update(precio=123)
        with self.assertRaisesRegex(ReplicaError, "fuera de la réplica"):
            self.sync()
        self.assertEqual(Producto.objects.using("offline").get().precio, 123)
        LocalCommand.objects.using("offline").create(operation_id=uuid4(), node=self.node,
            sequence=1, actor_id=self.user.pk, kind="future.sale", payload={}, fingerprint="x" * 64)
        with patch("local_pos.integrated_replica.capture") as source:
            with self.assertRaisesRegex(ReplicaError, "conciliar"):
                self.sync()
            source.assert_not_called()

    def test_wrong_branch_user_or_cloud_rejected_without_changing_active_copy(self):
        state = self.sync()
        with self.assertRaises(ReplicaError):
            synchronize(self.user.pk, self.other_point.pk)
        with patch("local_pos.integrated_replica.cloud_fingerprint", return_value="x" * 64):
            with self.assertRaises(ReplicaError):
                self.sync()
        self.assertEqual(ReplicaState.objects.using("offline").get().active_id, state.active_id)
        self.assertEqual(Producto.objects.using("offline").get().precio, 2000)

    def test_first_request_outside_employee_branch_is_denied(self):
        with self.assertRaises(ReplicaScopeError):
            synchronize(self.user.pk, self.other_point.pk)
        self.assertFalse(ReplicaState.objects.using("offline").exists())

    def test_revoked_user_blocks_offline_copy_without_deleting_data(self):
        state = self.sync()
        Usuario.objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(ReplicaScopeError):
            self.sync()
        state.refresh_from_db(using="offline")
        self.assertTrue(state.blocked)
        self.assertEqual(Producto.objects.using("offline").count(), 1)
        self.assertIsNone(authorized_identity(self.user))

    def test_losing_client_permission_removes_only_authorized_reference_clients(self):
        self.sync()
        from mainApp.permissions import user_can_access_url_name as actual_permission
        def permissions(user, name):
            return False if name == "cliente_autocomplete" else actual_permission(user, name)
        with patch("local_pos.integrated_replica.user_can_access_url_name", side_effect=permissions):
            state = self.sync()
        self.assertFalse(state.scope["clients"])
        self.assertEqual(Cliente.objects.using("offline").count(), 0)
        self.assertGreater(Cliente.objects.count(), 0)

    def test_read_only_source_rejects_accidental_business_write(self):
        def unsafe(scope):
            Sucursal.objects.update(nombre="must not persist")
        with patch("local_pos.integrated_replica.collect", side_effect=unsafe):
            with self.assertRaises(DatabaseError):
                self.sync()
        self.assertEqual(Sucursal.objects.get(pk=self.branch.pk).nombre, "Reference branch")
        self.assertFalse(ReplicaState.objects.using("offline").exists())

    def test_repeatable_read_is_consistent_during_concurrent_cloud_update(self):
        def read_while_source_changes(scope):
            def update():
                try:
                    Producto.objects.filter(pk=self.product.pk).update(precio=7000)
                finally:
                    connections.close_all()
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(update).result(timeout=10)
            return collect(scope)
        with patch("local_pos.integrated_replica.collect", side_effect=read_while_source_changes):
            self.sync()
        self.assertEqual(Producto.objects.using("offline").get().precio, 2000)
        self.assertEqual(Producto.objects.get().precio, 7000)
        self.sync()
        self.assertEqual(Producto.objects.using("offline").get().precio, 7000)

    def test_expired_capture_is_not_activated(self):
        identity, scope, rows, _ = capture(self.user.pk, self.point.pk)
        with self.assertRaises(ReplicaError):
            apply_snapshot(identity, scope, rows, timezone.now() - timedelta(minutes=6))
        self.assertFalse(ReplicaState.objects.using("offline").exists())

    def test_reference_router_does_not_move_cloud_sessions(self):
        from django.contrib.sessions.models import Session
        from .reference_runtime import ReferenceRouter
        router = ReferenceRouter()
        self.assertIsNone(router.db_for_write(Session))
        self.assertIsNone(router.db_for_write(Producto))
        self.assertEqual(router.db_for_write(ReplicaState), "offline")
        self.assertFalse(router.allow_migrate("default", "local_pos"))
        self.assertFalse(router.allow_migrate("offline", "mainApp"))

    def test_worker_failure_does_not_break_successful_sale_response(self):
        request = RequestFactory().post("/generar_venta/")
        request.user = self.user
        response = HttpResponse("already committed")
        with patch("local_pos.reference_runtime.worker.observe", side_effect=RuntimeError("scheduler down")):
            result = ReferenceRefreshMiddleware(lambda request: response)(request)
        self.assertIs(result, response)

    def test_prepare_view_uses_authenticated_user_and_rejects_foreign_point(self):
        request = RequestFactory().post("/local/copia/", {"point_id": self.point.pk, "user_id": 999})
        request.user = self.user
        with patch("local_pos.reference_views.worker.request") as schedule:
            self.assertEqual(index(request).status_code, 302)
            schedule.assert_called_once_with(self.user.pk, self.point.pk)
        request.POST = request.POST.copy()
        request.POST["point_id"] = self.other_point.pk
        self.assertEqual(index(request).status_code, 403)

    def test_status_exposes_counts_but_no_password_hash_or_credentials(self):
        self.sync()
        request = RequestFactory().get("/local/copia/?format=json")
        request.user = self.user
        response = index(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"product": 1', response.content)
        self.assertNotIn(self.user.password.encode(), response.content)
        self.assertNotIn(b"cloud_fingerprint", response.content)
        self.assertNotIn(b'"password"', response.content)

    def test_logout_does_not_renew_background_lease(self):
        worker = ReferenceWorker()
        with patch.object(worker, "_start"):
            worker.observe(self.user.pk)
        self.assertGreater(worker.lease_until, 0)
        worker.pause()
        self.assertEqual(worker.lease_until, 0)

    def test_reference_page_renders_original_navigation_and_live_status(self):
        self.sync()
        request = RequestFactory().get("/local/copia/")
        request.user = self.user
        response = index(request)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="reference-copy"')
        self.assertContains(response, 'data-reference-count="product">1</strong>')
        self.assertContains(response, "reference_copy.js")
        self.assertContains(response, "Esta etapa todavía no habilita ventas offline.")

    def test_queued_manual_request_is_reported_busy_without_duplicate_work(self):
        worker = ReferenceWorker()
        with patch.object(worker, "_start"):
            self.assertTrue(worker.request(self.user.pk, self.point.pk))
            self.assertTrue(worker.status()["busy"])
            self.assertFalse(worker.request(self.user.pk, self.point.pk))

    def test_simultaneous_download_is_rejected_by_database_lock(self):
        from threading import Event
        started, finish = Event(), Event()
        actual_capture = capture
        def slow_capture(*args):
            started.set()
            if not finish.wait(10):
                raise AssertionError("Test did not release fixture snapshot")
            return actual_capture(*args)
        def download():
            try:
                return self.sync()
            finally:
                connections.close_all()
        with patch("local_pos.integrated_replica.capture", side_effect=slow_capture):
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(download)
                try:
                    self.assertTrue(started.wait(10))
                    with self.assertRaisesRegex(ReplicaError, "actualización en curso"):
                        self.sync()
                finally:
                    finish.set()
                self.assertTrue(future.result(timeout=20).active_id)
        self.assertEqual(ReplicaTransfer.objects.using("offline").count(), 1)

    def test_status_never_reports_copy_ready_for_another_cloud(self):
        self.sync()
        request = RequestFactory().get("/local/copia/?format=json")
        request.user = self.user
        with patch("local_pos.reference_views.cloud_fingerprint", return_value="different-cloud"):
            response = index(request)
        import json
        self.assertFalse(json.loads(response.content)["ready"])

    def test_user_and_role_permission_links_are_refreshed_atomically(self):
        from mainApp.models import RolPermiso
        role_permission = Permiso.objects.create(nombre="fictional_role_permission")
        direct_permission = Permiso.objects.create(nombre="fictional_direct_permission")
        role_link = RolPermiso.objects.create(rol_id=self.user.rolid_id, permiso=role_permission)
        direct_link = UsuarioPermiso.objects.create(usuario=self.user, permiso=direct_permission, permitido=False)
        self.sync()
        self.assertTrue(RolPermiso.objects.using("offline").filter(rol_id=self.user.rolid_id, permiso_id=role_permission.pk).exists())
        self.assertFalse(UsuarioPermiso.objects.using("offline").get(usuario_id=self.user.pk, permiso_id=direct_permission.pk).permitido)
        role_link.delete()
        direct_link.delete()
        self.sync()
        self.assertFalse(RolPermiso.objects.using("offline").filter(rol_id=self.user.rolid_id).exists())
        self.assertFalse(UsuarioPermiso.objects.using("offline").filter(usuario_id=self.user.pk).exists())
