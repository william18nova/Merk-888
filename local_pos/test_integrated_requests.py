"""Real PostgreSQL / real sessions; cloud and fallback are both disposable."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import Mock, patch
from uuid import uuid4

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.sessions.models import Session
from django.core.management import get_commands, load_command_class
from django.core.cache import caches
from django.db import connections, connection, transaction, DatabaseError
from django.http import JsonResponse
from django.test import Client, RequestFactory, TransactionTestCase, override_settings
from django.urls import include, path
from django.utils import timezone

from mainApp.models import Usuario, Rol, Sucursal, Empleado, Permiso, UsuarioPermiso, RolPermiso
from .cloud_health import CloudMonitor, classify_error
from .connectivity import Health
from .integrated_auth import remember_user, authorized_identity, cloud_fingerprint
from .integrated_middleware import ConnectionMiddleware
from .models import LocalNode, ReplicaState, IntegratedIdentity, LocalCommand
from .request_context import database_context, offline_request


def test_login_view(request):
    user = authenticate(request, username=request.POST.get("username"), password=request.POST.get("password"))
    if not user:
        return JsonResponse({"success": False}, status=401)
    login(request, user)
    return JsonResponse({"user": user.pk})


def test_identity_view(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        database = cursor.fetchone()[0]
    request.session["read_count"] = request.session.get("read_count", 0) + 1
    return JsonResponse({"user": request.user.pk, "database": database,
                         "branches": list(Sucursal.objects.values_list("nombre", flat=True))})


def test_logout_view(request):
    logout(request)
    return JsonResponse({"success": True})


def test_accidental_get_write(request):
    Sucursal.objects.update(nombre="must not be saved")
    return JsonResponse({"success": True})


urlpatterns = [
    path("test/login/", test_login_view, name="login"),
    path("test/identity/", test_identity_view, name="integrated_test_identity"),
    path("test/logout/", test_logout_view, name="logout"),
    path("test/unsafe-get/", test_accidental_get_write, name="integrated_test_unsafe"),
    path("", include("NovaSoft.urls")),
]


@override_settings(ROOT_URLCONF=__name__)
class IntegratedRequestTests(TransactionTestCase):
    databases = {"default", "offline"}

    def _fixture_teardown(self):
        # Production routing forbids local schema changes. Test-only cleanup
        # must include every table created by DisposablePostgresRunner.
        with override_settings(DATABASE_ROUTERS=[]):
            super()._fixture_teardown()

    def setUp(self):
        for alias in ("default", "offline"):
            caches[alias].clear()
        self.node = LocalNode.objects.using("offline").create(id=settings.LOCAL_CONFIG["instance_id"])
        self.role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("integrated-test", "test-only", rolid=self.role)
        self.branch = Sucursal.objects.create(nombre="Cloud branch")
        Empleado.objects.create(usuarioid=self.user, sucursalid=self.branch, nombre="Test", apellido="Only",
                                numerodocumento="TEST-ONLY")
        self.health = patch("local_pos.integrated_middleware.monitor.snapshot", return_value=Health.AVAILABLE).start()
        self.addCleanup(patch.stopall)
        self.client = Client()

    def sign_in(self):
        response = self.client.post("/test/login/", {"username": "integrated-test", "password": "test-only"})
        self.assertEqual(response.status_code, 200, response.content)
        return response

    def test_online_login_can_renew_expired_identity_without_clearing_conflict(self):
        state = self.authorize_snapshot()
        expired = timezone.now() - timedelta(seconds=1)
        ReplicaState.objects.using("offline").update(expires_at=expired)
        IntegratedIdentity.objects.using("offline").update(expires_at=expired)
        LocalCommand.objects.using("offline").create(pk=uuid4(), node=self.node,
            actor_id=self.user.pk, sequence=1, kind="fixture", payload={}, state="conflict", fingerprint="f" * 64)
        self.sign_in()
        self.assertIsNotNone(authorized_identity(self.user))
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "conflict")
        state.refresh_from_db(using="offline")
        self.assertEqual(state.expires_at, expired)
        self.assertEqual(self.client.get("/test/identity/").status_code, 503)

    def test_logout_remains_available_with_expired_copy_and_conflict(self):
        self.authorize_snapshot()
        self.sign_in()
        self.health.return_value = Health.DISCONNECTED
        IntegratedIdentity.objects.using("offline").update(expires_at=timezone.now() - timedelta(seconds=1))
        LocalCommand.objects.using("offline").create(pk=uuid4(), node=self.node,
            actor_id=self.user.pk, sequence=1, kind="fixture", payload={}, state="conflict", fingerprint="f" * 64)
        response = self.client.post("/test/logout/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(LocalCommand.objects.using("offline").get().state, "conflict")

    def authorize_snapshot(self):
        remember_user(self.user, force=True)
        grant = IntegratedIdentity.objects.using("offline").get(user_id=self.user.pk)
        grant.allowed_routes = ["integrated_test_identity"]
        grant.save(using="offline")
        return ReplicaState.objects.using("offline").create(node=self.node, local_user_id=self.user.pk,
            server="http://fictional.invalid", device_id=uuid4(), active_id=uuid4(),
            expires_at=timezone.now() + timedelta(hours=2), scope={"routes": grant.allowed_routes})

    def test_cloud_login_uses_only_local_session_store_and_mirrors_same_identity(self):
        response = self.sign_in()
        self.assertEqual(response["X-Nova-Mode"], "online")
        self.assertEqual(Session.objects.using("default").count(), 0)
        self.assertEqual(Session.objects.using("offline").count(), 1)
        actor = Usuario.objects.using("offline").get(pk=self.user.pk)
        self.user.refresh_from_db()
        self.assertEqual(actor.password, self.user.password)
        self.assertEqual(actor.get_session_auth_hash(), self.user.get_session_auth_hash())
        self.assertEqual(actor.empleado.sucursalid_id, self.branch.pk)
        self.assertIsNotNone(authorized_identity(actor))

    def test_original_login_page_authenticates_online_and_offline(self):
        form = {"nombreusuario": "integrated-test", "contraseña": "test-only"}
        response = self.client.post("/", form)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/test/identity/").json()["user"], self.user.pk)
        self.assertEqual(Session.objects.using("default").count(), 0)
        self.authorize_snapshot()
        self.client.post("/test/logout/")
        self.health.return_value = Health.DISCONNECTED
        response = self.client.post("/", form)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get("/test/identity/").json()["user"], self.user.pk)

    def test_same_cookie_survives_cloud_offline_cloud_without_relogin(self):
        self.sign_in()
        self.authorize_snapshot()
        cookie = self.client.cookies[settings.SESSION_COOKIE_NAME].value
        Sucursal.objects.using("offline").filter(pk=self.branch.pk).update(nombre="Local branch")
        online = self.client.get("/test/identity/").json()
        self.assertEqual(online["database"], "test_nova_hybrid_ci")
        self.assertEqual(online["branches"], ["Cloud branch"])
        self.health.return_value = Health.DISCONNECTED
        offline = self.client.get("/test/identity/")
        self.assertEqual(offline.status_code, 200, offline.content)
        self.assertEqual(offline["X-Nova-Mode"], "offline")
        self.assertEqual(offline.json()["database"], "test_nova_integrated_ci")
        self.assertEqual(offline.json()["branches"], ["Local branch"])
        self.assertEqual(offline.json()["user"], self.user.pk)
        self.assertEqual(self.client.cookies[settings.SESSION_COOKIE_NAME].value, cookie)
        self.health.return_value = Health.AVAILABLE
        again = self.client.get("/test/identity/")
        self.assertEqual(again.json(), online)
        self.assertEqual(self.client.session["read_count"], 3)

    def test_no_offline_writes_or_unapproved_routes(self):
        self.sign_in()
        self.authorize_snapshot()
        self.health.return_value = Health.DISCONNECTED
        for method, url in (("post", "/test/identity/"), ("get", "/agregar_producto/"), ("post", "/generar_venta/")):
            with self.subTest(method=method, url=url):
                response = getattr(self.client, method)(url, HTTP_ACCEPT="application/json")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json()["code"], "offline_route_not_ready")

    def test_approved_get_cannot_write_even_if_view_has_an_unexpected_side_effect(self):
        self.sign_in()
        state = self.authorize_snapshot()
        state.scope["routes"].append("integrated_test_unsafe")
        state.save(using="offline")
        IntegratedIdentity.objects.using("offline").filter(user_id=self.user.pk).update(allowed_routes=state.scope["routes"])
        self.health.return_value = Health.DISCONNECTED
        with self.assertRaises(DatabaseError):
            self.client.get("/test/unsafe-get/")
        self.assertEqual(Sucursal.objects.using("offline").get(pk=self.branch.pk).nombre, "Cloud branch")
        self.assertEqual(Sucursal.objects.get(pk=self.branch.pk).nombre, "Cloud branch")

    def test_expired_snapshot_does_not_silently_fall_back(self):
        self.sign_in()
        state = self.authorize_snapshot()
        state.expires_at = timezone.now() - timedelta(seconds=1)
        state.save(using="offline")
        self.health.return_value = Health.DISCONNECTED
        self.assertEqual(self.client.get("/test/identity/").status_code, 503)

    def test_expired_identity_cannot_authenticate_offline(self):
        self.sign_in()
        self.authorize_snapshot()
        IntegratedIdentity.objects.using("offline").update(expires_at=timezone.now() - timedelta(seconds=1))
        self.health.return_value = Health.DISCONNECTED
        self.assertEqual(self.client.get("/test/identity/").status_code, 503)
        response = Client().post("/test/login/", {"username": "integrated-test", "password": "test-only"})
        self.assertEqual(response.status_code, 401)

    def test_offline_login_and_logout_work_with_bounded_identity(self):
        self.authorize_snapshot()
        self.health.return_value = Health.DISCONNECTED
        self.sign_in()
        self.assertEqual(self.client.get("/test/identity/").json()["user"], self.user.pk)
        self.assertEqual(self.client.post("/test/logout/").status_code, 200)
        self.assertEqual(Session.objects.using("offline").count(), 0)

    def test_online_permission_snapshot_replaces_revoked_grants(self):
        permission = Permiso.objects.create(nombre="integrated_test_read")
        role_link = RolPermiso.objects.create(rol=self.role, permiso=permission)
        direct = UsuarioPermiso.objects.create(usuario=self.user, permiso=permission, permitido=False)
        remember_user(self.user, force=True)
        self.assertTrue(RolPermiso.objects.using("offline").filter(rol_id=self.role.pk, permiso_id=permission.pk).exists())
        self.assertFalse(UsuarioPermiso.objects.using("offline").get(usuario_id=self.user.pk).permitido)
        role_link.delete()
        direct.delete()
        Usuario.objects.filter(pk=self.user.pk).update(is_active=False)
        remember_user(self.user, force=True)
        self.assertFalse(RolPermiso.objects.using("offline").filter(rol_id=self.role.pk).exists())
        self.assertFalse(UsuarioPermiso.objects.using("offline").filter(usuario_id=self.user.pk).exists())
        self.assertIsNone(authorized_identity(self.user))

    def test_grant_from_another_cloud_is_not_accepted(self):
        self.authorize_snapshot()
        IntegratedIdentity.objects.using("offline").update(cloud_fingerprint="0" * 64)
        self.assertIsNone(authorized_identity(self.user))

    def test_pending_queue_blocks_resuming_cloud_before_sync(self):
        self.sign_in()
        self.authorize_snapshot()
        LocalCommand.objects.using("offline").create(operation_id=uuid4(), node=self.node, sequence=1,
            actor_id=self.user.pk, kind="test", payload={}, fingerprint="f" * 64)
        response = self.client.get("/test/identity/", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "reconcile_before_cloud")

    def test_unknown_or_rejected_cloud_is_not_treated_as_outage(self):
        self.authorize_snapshot()
        for health in (Health.UNKNOWN, Health.REJECTED):
            self.health.return_value = health
            self.assertEqual(self.client.get("/test/identity/").status_code, 503)

    def test_identity_copy_error_does_not_repeat_successful_cloud_write(self):
        self.sign_in()
        with patch("local_pos.integrated_auth.remember_user", side_effect=DatabaseError("fictional local failure")):
            response = self.client.get("/test/identity/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Nova-Offline-Identity"], "unavailable")
        self.assertEqual(self.client.session["read_count"], 1)

    def test_database_context_is_restored_after_exception_and_rejects_nesting(self):
        cloud = connections["default"]
        with self.assertRaisesRegex(ValueError, "test"):
            with database_context("offline"):
                self.assertIs(connection._connections["default"], connections["offline"])
                self.assertTrue(offline_request())
                with self.assertRaises(RuntimeError):
                    with database_context("default"):
                        pass
                raise ValueError("test")
        self.assertIs(connections["default"], cloud)
        self.assertFalse(offline_request())

    def test_context_cannot_switch_in_atomic_or_manual_transaction(self):
        with transaction.atomic():
            with self.assertRaises(RuntimeError):
                with database_context("offline"):
                    pass
        connection.set_autocommit(False)
        try:
            with self.assertRaises(RuntimeError):
                with database_context("offline"):
                    pass
        finally:
            connection.rollback()
            connection.set_autocommit(True)

    def test_cache_and_connections_are_isolated_between_concurrent_threads(self):
        caches["default"].set("same", "cloud")
        caches["offline"].set("same", "local")
        barrier = Barrier(2)
        def worker(alias):
            try:
                with database_context(alias):
                    barrier.wait(timeout=5)
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT current_database()")
                        return cursor.fetchone()[0], caches["default"].get("same")
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, ("default", "offline")))
        self.assertEqual(results, [("test_nova_hybrid_ci", "cloud"), ("test_nova_integrated_ci", "local")])

    def test_failed_cloud_request_is_never_retried_offline(self):
        handler = Mock(side_effect=DatabaseError("connection interrupted after possible commit"))
        with self.assertRaises(DatabaseError):
            ConnectionMiddleware(handler)(RequestFactory().post("/generar_venta/"))
        handler.assert_called_once()
        self.assertFalse(offline_request())

    def test_actual_cloud_probe_sees_receipt_table_without_mutating_data(self):
        self.assertEqual(CloudMonitor()._probe(), Health.AVAILABLE)

    def test_integrated_runserver_overrides_staticfiles_command(self):
        self.assertEqual(get_commands()["runserver"], "local_pos")
        command = load_command_class("local_pos", "runserver")
        self.health.return_value = Health.DISCONNECTED
        with patch("django.core.management.base.BaseCommand.check_migrations") as original:
            command.check_migrations()
            original.assert_not_called()

    def test_authentication_errors_are_not_connection_loss(self):
        self.assertEqual(classify_error(Exception("password authentication failed")), Health.REJECTED)
        self.assertEqual(classify_error(Exception("connection refused")), Health.DISCONNECTED)
