"""Contrato del transporte de Generar Venta original, sin BD de producción."""
import json
import re
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from hybrid_client.test_client import ClientTests
from hybrid_client.sale_page import render_page, static_file, read_endpoint, BUNDLE
from hybrid_client.server import make_server
from pos_shared.protocol import ProtocolError


class OriginalSaleTests(unittest.TestCase):
    setUp = ClientTests.setUp
    sale = ClientTests.sale
    def test_original_template_and_assets(self):
        self.assertTrue((BUNDLE / "index.html").is_file(), "Ejecuta build_hybrid_sale_assets.py")
        page = render_page(self.client, "NONCE").decode()
        for marker in ('id="venta-form"', 'id="producto_busqueda_nombre"', 'id="codigo_o_barras"',
                       'id="myModal"', 'javascript/generar_venta.js', 'nonce="NONCE"'):
            self.assertIn(marker, page)
        self.assertNotIn("__HYBRID_CONTEXT__", page)
        self.assertNotIn(self.client.store.get("config")["secret"], page)
        self.assertIsNone(static_file("/static/../../NovaSoft/settings.py"))
        self.assertEqual(static_file("/static/javascript/generar_venta.js")[1], "text/javascript")
        original = Path(__file__).resolve().parents[1] / "mainApp/static/javascript/generar_venta.js"
        self.assertEqual(static_file("/static/javascript/generar_venta.js")[0], original.read_bytes())

    def test_names_are_escaped_in_html_and_bootstrap(self):
        session = self.client.store.get("session")
        session["user"] = '</script><script>alert("XSS")</script>'
        session["branch"] = '<img src=x onerror="alert(1)">'
        self.client.store.set("session", session)
        page = render_page(self.client, "NONCE").decode()
        self.assertNotIn(session["user"], page)
        self.assertNotIn(session["branch"], page)
        self.assertIn('\\u003c/script\\u003e', page)

    def test_locked_session_cannot_render(self):
        self.client.unlocked = False
        with self.assertRaises(ProtocolError):
            render_page(self.client, "test")

    def test_stock_negative_and_signed_quotes_not_exposed(self):
        data = read_endpoint(self.client, "verificar_producto", {"producto_id": "10", "cantidad": "500"})
        self.assertTrue(data["exists"])
        self.assertEqual(data["cantidad_disponible"], -5)
        snapshot = read_endpoint(self.client, "producto_snapshot", {})
        self.assertNotIn("quote", snapshot["results"][0])
        self.assertEqual(read_endpoint(self.client, "producto_autocomplete", {"term": "tomate"})["results"][0]["id"], 10)

    def test_http_nonce_origin_and_operation_scope(self):
        server = make_server(self.client, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base + "/generar_venta/") as response:
            page = response.read().decode()
            self.assertIn("script-src 'self' 'nonce-", response.headers["Content-Security-Policy"])
        token = re.search(r'name="local-token" content="([^"]+)"', page)[1]
        headers = {"Origin": base, "X-Local-Token": token, "Content-Type": "application/x-www-form-urlencoded"}
        request = Request(base + "/_sale/verificar_producto/", b"producto_id=10&cantidad=500", headers)
        self.assertTrue(json.load(urlopen(request))["exists"])
        request = Request(base + "/_sale/verificar_producto/", b"producto_id=10", {**headers, "Origin": "https://evil.test"})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)
        self.cloud.offline = True
        row = self.client.checkout(self.sale())
        self.assertEqual(json.load(urlopen(base + "/api/operation/" + row["id"]))["id"], row["id"])
        session = self.client.store.get("session")
        session["session_id"] = "other"
        self.client.store.set("session", session)
        with self.assertRaises(HTTPError) as error:
            urlopen(base + "/api/operation/" + row["id"])
        self.assertEqual(error.exception.code, 404)
