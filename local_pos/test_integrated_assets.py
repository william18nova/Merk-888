"""UI assets are verified locally; no database or external network needed."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from django.http import HttpResponse
from django.test import override_settings, RequestFactory, SimpleTestCase

from .integrated_assets import asset_root, load_assets, LocalAssetsMiddleware


class IntegratedAssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.directory = asset_root(self.root) / "local_vendor"
        self.directory.mkdir(parents=True)
        self.name = "a" * 24 + ".js"
        self.remote = "https://code.jquery.com/jquery-3.7.1.min.js"
        self.local = "/static/local_vendor/" + self.name
        data = b"// fictional local asset"
        (self.directory / self.name).write_bytes(data)
        self.manifest = {"urls": {self.remote: self.local}, "integrity": {self.remote: {
            "file": self.name, "packaged_sha256": hashlib.sha256(data).hexdigest()}}}
        self.write_manifest()

    def write_manifest(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_loads_verified_files_without_network(self):
        self.assertEqual(load_assets(self.root), {self.remote: self.local})

    def test_missing_or_modified_file_is_not_accepted(self):
        (self.directory / self.name).write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "prepare_integrated_assets"):
            load_assets(self.root)
        (self.directory / self.name).unlink()
        with self.assertRaises(RuntimeError):
            load_assets(self.root)

    def test_traversal_and_unverified_target_are_rejected(self):
        self.manifest["integrity"][self.remote]["file"] = "../outside.js"
        self.write_manifest()
        with self.assertRaises(RuntimeError):
            load_assets(self.root)
        self.manifest["integrity"][self.remote]["file"] = self.name
        self.manifest["urls"][self.remote] = "/static/unverified.js"
        self.write_manifest()
        with self.assertRaises(RuntimeError):
            load_assets(self.root)

    def test_original_html_and_lazy_loader_use_local_urls_without_font_requests(self):
        from django.conf import settings
        if not settings.configured:
            settings.configure(DEFAULT_CHARSET="utf-8")
        html = ('<script src="' + self.remote + '"></script>'
                '<link href="https://fonts.googleapis.com/css2?family=Inter">'
                '<script>const lazy = "' + self.remote + '";</script>')
        response = HttpResponse(html)
        response["Content-Length"] = str(len(response.content))
        response["Content-Security-Policy"] = "object-src 'none'"
        with override_settings(NOVA_INTEGRATED_ASSETS={self.remote: self.local}):
            result = LocalAssetsMiddleware(lambda request: response)(object())
        self.assertNotIn(self.remote.encode(), result.content)
        self.assertEqual(result.content.count(self.local.encode()), 2)
        self.assertNotIn(b"fonts.googleapis", result.content)
        self.assertEqual(int(result["Content-Length"]), len(result.content))
        self.assertIn("object-src 'none'", result["Content-Security-Policy"])
        self.assertIn("font-src 'self'", result["Content-Security-Policy"])


@override_settings(DEBUG=False, NOVA_INTEGRATED_POS_ENABLED=True)
class IntegratedStaticRoutingTests(SimpleTestCase):
    def handler(self, *, use_static=True):
        from .management.commands.runserver import Command
        app = Mock(side_effect=AssertionError("Static request must not reach DB/auth middleware"))
        with patch("django.core.management.commands.runserver.Command.get_handler", return_value=app):
            handler = Command().get_handler(use_static_handler=use_static, insecure_serving=False)
        return handler, app

    def fetch(self, handler, path):
        captured = {}
        def start(status, headers, exc_info=None):
            captured.update(status=int(status.split()[0]), headers=dict(headers))
        response = handler(RequestFactory().get(path).environ, start)
        try:
            body = b"".join(response)
        finally:
            response.close()
        return captured, body

    def test_css_js_image_without_session_database_or_debug(self):
        from django.conf import settings
        from django.contrib.staticfiles import finders
        handler, app = self.handler()
        for name, content_type in (("css/navbar.css", "text/css"),
                ("javascript/generar_venta.js", "javascript"), ("images/logoNovaAdvance.png", "image/png")):
            response, body = self.fetch(handler, "/static/" + name + "?cold=1")
            self.assertEqual(response["status"], 200)
            self.assertIn(content_type, response["headers"]["Content-Type"])
            self.assertEqual(body, Path(finders.find(name)).read_bytes())
        self.assertFalse(settings.DEBUG)
        app.assert_not_called()

    def test_missing_file_and_path_escape_never_reach_application_or_expose_sources(self):
        handler, app = self.handler()
        for path in ("/static/not-a-real-file.css", "/static/../NovaSoft/settings.py", "/static/../.env"):
            response, body = self.fetch(handler, path)
            self.assertIn(response["status"], (400, 404))
            self.assertNotIn(b"DATABASES", body)
        app.assert_not_called()

    def test_normal_app_and_explicit_nostatic_keep_original_behavior(self):
        handler, app = self.handler(use_static=False)
        self.assertIs(handler, app)
        with override_settings(NOVA_INTEGRATED_POS_ENABLED=False):
            handler, app = self.handler()
            self.assertIs(handler, app)
