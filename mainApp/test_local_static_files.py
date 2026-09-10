"""Evita que runserver entregue un cierre antiguo o JavaScript truncado."""

import ast
from types import SimpleNamespace

from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from whitenoise.middleware import WhiteNoiseMiddleware


class LocalStaticFilesTests(SimpleTestCase):
    def test_local_static_refresh_does_not_enable_production_debug(self):
        names = {"WHITENOISE_AUTOREFRESH", "WHITENOISE_USE_FINDERS", "WHITENOISE_MAX_AGE"}
        source = (settings.BASE_DIR / "NovaSoft/settings.py").read_text(encoding="utf-8")
        assignments = [
            node for node in ast.parse(source).body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id in names for target in node.targets)
        ]
        self.assertEqual(len(assignments), 3)
        code = compile(ast.Module(body=assignments, type_ignores=[]), "static-settings", "exec")
        for argv, debug, expected in (
            (["manage.py", "runserver"], False, True),
            (["uwsgi"], False, False),
            (["manage.py", "test"], False, False),
            (["manage.py", "runserver"], True, True),
        ):
            with self.subTest(argv=argv, debug=debug):
                namespace = {"DEBUG": debug, "sys": SimpleNamespace(argv=argv)}
                exec(code, namespace)
                self.assertEqual(namespace["WHITENOISE_AUTOREFRESH"], expected)
                self.assertEqual(namespace["WHITENOISE_USE_FINDERS"], expected)
                self.assertEqual(namespace["WHITENOISE_MAX_AGE"], 0 if expected else 60)
                self.assertEqual(namespace["DEBUG"], debug)

    @override_settings(
        DEBUG=False, WHITENOISE_AUTOREFRESH=True,
        WHITENOISE_USE_FINDERS=True, WHITENOISE_MAX_AGE=0,
    )
    def test_runserver_serves_the_complete_current_cash_closing_script(self):
        middleware = WhiteNoiseMiddleware(lambda request: HttpResponse(status=404))
        request = RequestFactory().get("/static/javascript/turno_caja.js?v=24")
        response = middleware(request)
        try:
            self.assertEqual(response.status_code, 200)
            content = b"".join(response.streaming_content)
            expected = (settings.BASE_DIR / "mainApp/static/javascript/turno_caja.js").read_bytes()
            self.assertEqual(content, expected)
            self.assertEqual(int(response["Content-Length"]), len(content))
            self.assertIn(b"navigateClosePage", content)
            self.assertIn("max-age=0", response["Cache-Control"])
        finally:
            response.close()
