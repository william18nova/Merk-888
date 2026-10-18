"""Navbar status is authenticated, read-only and uses the selected database."""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.db import OperationalError
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from .connection_status import database_status, status_for_mode
from .middleware import PagePermissionMiddleware
from .permissions import user_can_access_url_name


class DatabaseStatusTests(SimpleTestCase):
    def setUp(self):
        self.request = RequestFactory().get("/api/estado-conexion/", HTTP_ACCEPT="application/json")
        self.request.user = SimpleNamespace(is_authenticated=True)
        self.request.resolver_match = resolve(self.request.path)

    @override_settings(NOVA_INTEGRATED_POS_ENABLED=False)
    def test_normal_install_probes_database_read_only(self):
        with patch("mainApp.connection_status.connection") as database:
            cursor = database.cursor.return_value.__enter__.return_value
            cursor.fetchone.return_value = (1,)
            result = database_status(self.request)
            cursor.execute.assert_called_once_with("SELECT 1")
        self.assertEqual(json.loads(result.content)["mode"], "online")
        self.assertIn("no-store", result["Cache-Control"])
        self.assertEqual(reverse("database_connection_status"), self.request.path)

    def test_modes_follow_request_without_an_extra_cloud_monitor(self):
        for mode in ("online", "offline", "synchronizing"):
            with self.subTest(mode=mode), patch("mainApp.connection_status.connection") as database:
                self.request.nova_mode = mode
                database.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
                result = database_status(self.request)
                self.assertEqual(json.loads(result.content), status_for_mode(mode))

    def test_failure_never_claims_local_or_leaks_connection_details(self):
        self.request.nova_mode = "offline"
        with patch("mainApp.connection_status.connection") as database:
            database.cursor.side_effect = OperationalError("SECRET host password")
            response = database_status(self.request)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.content)["mode"], "unavailable")
        self.assertNotIn(b"SECRET", response.content)

    def test_only_authenticated_get_and_all_cashiers_have_access(self):
        self.assertTrue(user_can_access_url_name(self.request.user, "database_connection_status"))
        guard = PagePermissionMiddleware(lambda request: HttpResponse())
        self.assertIsNone(guard.process_view(self.request, database_status, [], {}))
        self.request.user.is_authenticated = False
        with patch("mainApp.connection_status.connection") as database:
            self.assertEqual(database_status(self.request).status_code, 401)
            database.cursor.assert_not_called()
        response = guard.process_view(self.request, database_status, [], {})
        self.assertEqual(response.status_code, 401)
        self.request.method = "POST"
        self.assertEqual(database_status(self.request).status_code, 405)

    @override_settings(NOVA_INTEGRATED_POS_ENABLED=True)
    def test_missing_hybrid_routing_never_assumes_online(self):
        with patch("mainApp.connection_status.connection") as database:
            database.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
            result = database_status(self.request)
        self.assertEqual(json.loads(result.content)["mode"], "unknown")

    def test_indicator_is_in_visible_brand_and_not_logout_area(self):
        html = render_to_string("navbar.html", {
            "user": self.request.user, "nav_connection": status_for_mode("offline"),
        })
        self.assertIn('data-mode="offline"', html)
        self.assertIn('role="status"', html)
        self.assertIn('aria-live="polite"', html)
        self.assertLess(html.index('id="nvConnection"'), html.index('class="nv__actions"'))
        self.request.user.is_authenticated = False
        anonymous = render_to_string("navbar.html", {"user": self.request.user})
        self.assertNotIn('id="nvConnection"', anonymous)

    def test_offline_permissions_allow_only_read_of_status(self):
        from local_pos.integrated_navigation import read_available
        self.assertTrue(read_available("database_connection_status", Mock(allowed_routes=[]), Mock(scope={})))

    @override_settings(HYBRID_LOCAL_ENABLED=True, NOVA_INTEGRATED_POS_ENABLED=True)
    @override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "local_pos"])
    def test_unavailable_middleware_header_does_not_claim_usable_local_mode(self):
        from local_pos.integrated_middleware import unavailable
        for request_mode, reported in (("offline", "unavailable"), ("blocked", "blocked"), ("online", "unavailable")):
            self.request.nova_mode = request_mode
            response = unavailable(self.request, "Prueba")
            self.assertEqual(response["X-Nova-Mode"], reported)

    @override_settings(HYBRID_LOCAL_ENABLED=True, NOVA_INTEGRATED_POS_ENABLED=True,
                       NOVA_INTEGRATED_JOURNAL_ENABLED=True)
    @override_settings(INSTALLED_APPS=[*settings.INSTALLED_APPS, "local_pos"])
    def test_real_routing_decision_reports_online_local_and_synchronizing(self):
        from local_pos.connectivity import Health, LocalState
        from local_pos.integrated_middleware import ConnectionMiddleware
        for health, pending, expected_mode, expected_alias in (
            (Health.AVAILABLE, 0, "online", "default"),
            (Health.DISCONNECTED, 0, "offline", "offline"),
            (Health.AVAILABLE, 1, "synchronizing", "offline"),
        ):
            with self.subTest(mode=expected_mode), \
                    patch("local_pos.integrated_middleware.monitor.snapshot", return_value=health), \
                    patch("local_pos.integrated_middleware.local_state", return_value=LocalState(
                        prepared=True, authorized=True, pending=pending)), \
                    patch("local_pos.integrated_middleware.database_context") as context, \
                    patch("mainApp.connection_status.connection") as database:
                database.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
                response = ConnectionMiddleware(database_status)(self.request)
                context.assert_called_once_with(expected_alias)
                self.assertEqual(json.loads(response.content)["mode"], expected_mode)
                self.assertEqual(response["X-Nova-Mode"], expected_mode)
