"""Local visual fixture: no database access, accounts or business operations."""
import os
import sys
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit, parse_qs
import json

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.test_settings"
import django
django.setup()
from django.template.loader import render_to_string
from mainApp.connection_status import status_for_mode


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "mainApp"), **kwargs)

    def do_GET(self):
        query = parse_qs(urlsplit(self.path).query)
        if self.path.startswith("/static/"):
            return super().do_GET()
        if self.path.startswith("/api/estado-conexion/"):
            referer = parse_qs(urlsplit(self.headers.get("Referer", "")).query)
            data = json.dumps(status_for_mode(referer.get("mode", ["online"])[0])).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            if "frame" in query:
                nav = render_to_string("navbar.html", {
                    "user": SimpleNamespace(is_authenticated=True),
                    "nav_session_name": "Usuario de prueba", "nav_session_username": "prueba",
                    "nav_connection": status_for_mode(query.get("mode", ["online"])[0]),
                    "nav_menu": [{"label": name, "url": "#"} for name in
                        ("Inicio", "Métricas", "Productos", "Inventarios", "Ventas", "Caja", "Visor Barcode")],
                })
                html = ('<!doctype html><html lang="es"><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                    '<link rel="stylesheet" href="/static/css/navbar.css"></head>'
                    '<body style="background:#1d3e69;color:white;font-family:system-ui">' + nav +
                    '<main class="page-wrap"><p>Vista de prueba sin datos reales.</p></main>'
                    '<script src="/static/javascript/navbar.js"></script>'
                    '<script src="/static/javascript/database_status.js"></script></body></html>')
            else:
                html = ('<!doctype html><html><head><title>Prueba del indicador de conexión</title></head>'
                    '<body style="background:#0d1f36;color:white;font-family:system-ui;margin:18px">'
                    '<h2>Indicador de base de datos · prueba visual</h2><h3>Escritorio</h3>'
                    '<iframe title="Desktop" style="width:100%;height:140px;border:0" src="/?frame=1&mode=online"></iframe>'
                    '<h3>Móvil · 320 px y 390 px</h3><div style="display:flex;gap:20px;flex-wrap:wrap">'
                    '<iframe title="Móvil local" style="width:320px;height:180px;border:1px solid #54749a" src="/?frame=1&mode=offline"></iframe>'
                    '<iframe title="Móvil sincronizando" style="width:390px;height:180px;border:1px solid #54749a" src="/?frame=1&mode=synchronizing"></iframe>'
                    '</div></body></html>')
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8942), Handler).serve_forever()
