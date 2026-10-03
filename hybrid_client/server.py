"""Servidor exclusivo de loopback; nunca expone el secreto del dispositivo al navegador."""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from pos_shared.protocol import ProtocolError, canonical, price_cart
from .client import RemoteError

ASSETS = Path(__file__).with_name("assets")


class LocalHTTPServer(ThreadingHTTPServer):
    # En Windows SO_REUSEADDR puede permitir dos procesos en el mismo puerto.
    allow_reuse_address = False


def make_server(client, port=8792):
    nonce = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # No registrar credenciales, carritos ni cuerpos HTTP.

        def reply(self, status, value, content_type="application/json; charset=utf-8"):
            body = canonical(value).encode() if content_type.startswith("application/json") else value
            self.send_response(status)
            for key, val in {
                "Content-Type": content_type, "Content-Length": str(len(body)), "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    f"default-src 'self'; script-src 'self' 'nonce-{nonce}'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                    if urlsplit(self.path).path == "/generar_venta/" else
                    "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
                ),
            }.items():
                self.send_header(key, val)
            try:
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                # Cerrar/recargar la pestaña puede cortar la respuesta después
                # de guardar. No convertir eso en otro intento ni un error 500.
                self.close_connection = True

        def trusted(self):
            return (self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"
                    and self.headers.get("Sec-Fetch-Site", "none") in {"none", "same-origin"})

        def do_GET(self):
            if not self.trusted():
                return self.reply(403, {"error": "Origen no permitido."})
            from .sale_page import render_page, static_file, read_endpoint, query_values
            parsed = urlsplit(self.path)
            if parsed.path == "/generar_venta/":
                try:
                    return self.reply(200, render_page(client, nonce), "text/html; charset=utf-8")
                except ProtocolError as exc:
                    return self.reply(400, {"error": str(exc)})
            if parsed.path.startswith("/static/"):
                asset = static_file(parsed.path)
                return self.reply(200, asset[0], asset[1]) if asset else self.reply(404, {"error": "No encontrado."})
            if parsed.path.startswith(("/_sale/", "/api/operation/")):
                if not client.unlocked:
                    return self.reply(401, {"error": "Desbloquea la sesión local."})
                try:
                    if parsed.path.startswith("/api/operation/"):
                        with client.lock:
                            row = client.store.operation(parsed.path.removeprefix("/api/operation/"))
                            if row and json.loads(row["payload"])["session_id"] == (client.store.get("session") or {}).get("session_id"):
                                return self.reply(200, client.public_operation(row))
                            return self.reply(404, {"error": "No registrada en esta sesión."})
                    # Buscar productos no espera al envío de pendientes a la nube.
                    return self.reply(200, read_endpoint(client, parsed.path.split("/")[2], query_values(parsed.query)))
                except (ValueError, ProtocolError) as exc:
                    return self.reply(400, {"error": str(exc)})
            if self.path == "/health":
                return self.reply(200, {"application": "nova-hybrid-pilot"})
            if self.path == "/api/status":
                return self.reply(200, client.status())
            if self.path in {"/api/products", "/api/history", "/api/printer"}:
                if not client.unlocked:
                    return self.reply(401, {"error": "Desbloquea la sesión local."})
                if self.path == "/api/printer":
                    return self.reply(200, client.printer.config())
                if self.path == "/api/products":
                    return self.reply(200, [{k: row[k] for k in ("id", "name", "barcode", "price", "stock")} for row in client.store.products().values()])
                session = client.store.get("session") or {}
                rows = client.store.history(session=session.get("session_id"))
                return self.reply(200, [client.public_operation(row) for row in rows])
            files = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css"),
                     "/sale-bridge.js": ("sale-bridge.js", "text/javascript"), "/sale-bridge.css": ("sale-bridge.css", "text/css")}
            if self.path not in files:
                return self.reply(404, {"error": "No encontrado."})
            name, mime = files[self.path]
            body = (ASSETS / name).read_bytes()
            if name == "index.html":
                body = body.replace(b"__LOCAL_NONCE__", nonce.encode())
            return self.reply(200, body, mime + "; charset=utf-8")

        def do_POST(self):
            verify_form = self.path == "/_sale/verificar_producto/"
            if (not self.trusted() or self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}"
                    or not secrets.compare_digest(self.headers.get("X-Local-Token", ""), nonce)
                    or self.headers.get("Content-Type", "").split(";")[0] != ("application/x-www-form-urlencoded" if verify_form else "application/json")):
                return self.reply(403, {"error": "Solicitud local no autorizada."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 100000:
                    raise ProtocolError("Solicitud demasiado grande o vacía.")
                raw = self.rfile.read(length)
                if verify_form:
                    from .sale_page import read_endpoint, query_values
                    if not client.unlocked:
                        return self.reply(401, {"error": "Desbloquea la sesión local."})
                    return self.reply(200, read_endpoint(client, "verificar_producto", query_values(raw.decode())))
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ProtocolError("Solicitud inválida.")
                with client.lock:
                    public = {"/api/enroll": client.enroll, "/api/start": client.start, "/api/unlock": client.unlock}
                    if self.path in public:
                        result = public[self.path](data)
                    else:
                        if not client.unlocked:
                            return self.reply(401, {"error": "Desbloquea la sesión local."})
                        if self.path in {"/api/checkout", "/api/release", "/api/print", "/api/printer", "/api/backup"} and data.get("session_id") != (client.store.get("session") or {}).get("session_id"):
                            raise ProtocolError("La sesión cambió en otra pestaña. Recarga antes de continuar; no se registró otra venta.")
                        if self.path == "/api/checkout":
                            result = client.checkout(data)
                        elif self.path == "/api/backup":
                            result = client.export_backup(data)
                        elif self.path == "/api/printer":
                            result = client.printer.configure(data)
                        elif self.path == "/api/print":
                            result = client.printer.enqueue(str(data.get("operation_id", "")), request_id=data.get("request_id"),
                                                           confirm_copy=data.get("confirm_copy") is True)
                        elif self.path == "/api/preview":
                            details, total = price_cart(data.get("items"), client.store.products())
                            result = {"total": str(total), "details": [{k: str(v) if k in {"subtotal", "precio_unitario"} else v for k, v in row.items()} for row in details]}
                        elif self.path == "/api/sync":
                            client.synchronize(retry_conflicts=True)
                            result = client.status()
                        elif self.path == "/api/release":
                            result = client.release()
                        elif self.path == "/api/lock":
                            client.unlocked = False
                            result = client.status()
                        else:
                            return self.reply(404, {"error": "Acción no encontrada."})
                self.reply(200, result, "application/octet-stream" if self.path == "/api/backup" else "application/json; charset=utf-8")
            except RemoteError as exc:
                self.reply(exc.status, {"error": str(exc)})
            except (ValueError, TypeError, KeyError, ProtocolError) as exc:
                self.reply(400, {"error": str(exc) or "Revisa los datos."})
            except Exception:
                self.reply(500, {"error": "No se pudo completar. Conserva el carrito y revisa el historial antes de repetir el cobro."})

    server = LocalHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def sync_worker(client, stop):
    while not stop.wait(20):
        if client.store.get("session"):
            try:
                client.synchronize()
            except Exception:
                client.online = False
                client.last_error = "No se pudo sincronizar. Los datos se conservan; solicita revisión si persiste."
