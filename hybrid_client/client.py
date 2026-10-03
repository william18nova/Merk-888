import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import UUID, uuid4

from pos_shared.protocol import PROTOCOL, ProtocolError, amount, canonical, price_cart
from . import VERSION
from .store import Store


class RemoteError(Exception):
    def __init__(self, message, status=503, code="unavailable"):
        super().__init__(message)
        self.status, self.code = status, code


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def cloud_url(raw, allow_local=False):
    url = urlsplit(str(raw).strip())
    local = allow_local and url.scheme == "http" and url.hostname in {"127.0.0.1", "localhost"}
    if (url.scheme != "https" and not local) or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
        raise ProtocolError("Usa la dirección HTTPS del POS, sin rutas, usuario ni contraseña.")
    return f"{url.scheme}://{url.netloc}"


class Client:
    def __init__(self, store: Store, *, allow_local=False, transport=None):
        self.store, self.allow_local, self.transport = store, allow_local, transport
        self.lock = threading.RLock()
        self.unlocked = False
        self.last_error = ""
        self.online = False
        self.pin_failures = 0
        self.pin_blocked_until = 0
        from .printing import PrinterManager
        self.printer = PrinterManager(self)

    def remote(self, action, data, *, anonymous=False):
        if self.transport:
            return self.transport(action, data)
        config = self.store.get("config")
        if not config:
            raise ProtocolError("Primero vincula este equipo.")
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": f"NovaPOS/{VERSION}"}
        if not anonymous:
            headers["Authorization"] = f"Bearer {config['device_id']}.{config['secret']}"
        request = Request(config["url"] + f"/api/hybrid/v1/{action}/", data=canonical(data).encode(), headers=headers, method="POST")
        try:
            with build_opener(NoRedirects()).open(request, timeout=5) as response:
                raw = response.read(16000001)
                if len(raw) > 16000000:
                    raise RemoteError("Respuesta del servidor demasiado grande.", 502)
                result = json.loads(raw)
                if not isinstance(result, dict) or result.get("ok") is not True:
                    raise RemoteError("El servidor no confirmó la operación.", 502)
                return result
        except HTTPError as exc:
            try:
                error = json.loads(exc.read(16000))
            except (ValueError, TypeError):
                error = {}
            if not isinstance(error, dict):
                error = {}
            raise RemoteError(error.get("error", "No se pudo confirmar con el servidor."), exc.code, error.get("code", "server")) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise RemoteError("Sin comunicación con la nube. Los pendientes siguen guardados en este equipo.") from None

    def enroll(self, data):
        with self.lock:
            if self.store.get("config", {}).get("device_id"):
                raise ProtocolError("El equipo ya está vinculado. No se puede reemplazar su identidad desde el asistente.")
            url = cloud_url(data.get("url"), self.allow_local)
            config = self.store.get("config", {})
            if config and config.get("url") != url:
                raise ProtocolError("Ya hay una vinculación iniciada para otro servidor; solicita revisión.")
            config = {"url": url, "secret": config.get("secret") or secrets.token_hex(32)}
            self.store.set("config", config)  # Persistir antes de transmitir; reintento seguro.
            result = self.remote("enroll", {"code": data.get("code"), "secret": config["secret"]}, anonymous=True)
            self.store.set("config", {**config, **{key: result[key] for key in ("device_id", "name", "point")}})
            return self.status()

    def start(self, data):
        with self.lock:
            if self.store.get("session") or self.store.pending():
                raise ProtocolError("Primero desbloquea, sincroniza y finaliza la sesión anterior.")
            pin = str(data.get("pin", ""))
            if len(pin) < 8 or len(pin) > 128:
                raise ProtocolError("Elige una clave local de 8 o más caracteres para recuperar esta sesión.")
            session_id = self.store.get("starting_session") or str(uuid4())
            self.store.set("starting_session", session_id)
            result = self.remote("session", {"username": data.get("username"), "password": data.get("password"), "session_id": session_id})
            salt = secrets.token_hex(16)
            self.store.set("pin", {"salt": salt, "hash": hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 300000).hex()})
            self.store.set("session", result)
            self.store.set("starting_session", None)
            self.store.set("ready", False)
            with self.store.connect() as db:
                db.execute("DELETE FROM products")
            self.unlocked = True
            self.refresh_catalog()
            return self.status()

    def unlock(self, data):
        if time.monotonic() < self.pin_blocked_until:
            raise ProtocolError("Espera un minuto antes de intentar la clave local otra vez.")
        saved = self.store.get("pin")
        if not saved or not self.store.get("session"):
            raise ProtocolError("No hay sesión local que recuperar.")
        derived = hashlib.pbkdf2_hmac("sha256", str(data.get("pin", "")).encode(), bytes.fromhex(saved["salt"]), 300000).hex()
        if not hmac.compare_digest(saved["hash"], derived):
            self.pin_failures += 1
            if self.pin_failures >= 5:
                self.pin_blocked_until = time.monotonic() + 60
            raise ProtocolError("Clave local incorrecta.")
        self.pin_failures, self.unlocked = 0, True
        return self.status()

    def status(self):
        config, session = self.store.get("config", {}), self.store.get("session")
        rows = self.store.pending()
        return {"version": VERSION, "paired": bool(config.get("device_id")), "name": config.get("name", "Equipo sin vincular"),
                "session": {k: v for k, v in (session or {}).items() if k not in {"ok"}} if self.unlocked else bool(session),
                "unlocked": self.unlocked, "ready": bool(self.store.get("ready")), "online": self.online,
                "pending": len(rows), "conflicts": sum(row["state"] == "conflict" for row in rows), "error": self.last_error,
                "catalog_updated": self.store.get("catalog_updated", "")}

    def export_backup(self, data):
        from .backup import encrypt, snapshot
        if not self.unlocked:
            raise ProtocolError("Desbloquea la sesión antes de respaldar.")
        self.unlock({"pin": data.get("pin", "")})
        password = data.get("password")
        if password != data.get("confirmation"):
            raise ProtocolError("Las contraseñas del respaldo no coinciden.")
        if password == data.get("pin"):
            raise ProtocolError("Usa una contraseña diferente de tu clave local para el respaldo.")
        with self.lock:
            document = snapshot(self.store.path)
        return encrypt(document, password)

    def refresh_catalog(self):
        session = self.store.get("session")
        if not session:
            return
        for _ in range(60):
            known = {pid: row["digest"] for pid, row in self.store.products().items()}
            result = self.remote("catalog", {"session_id": session["session_id"], "known": known})
            self.store.merge_catalog(result)
            if not result["more"]:
                self.store.set("catalog_updated", result["server_time"])
                self.store.set("ready", True)
                self.online = True
                return
        raise ProtocolError("El catálogo no terminó de sincronizar. No inicies ventas todavía.")

    def synchronize(self, *, catalog=True, retry_conflicts=False):
        with self.lock:
            active_row = None
            try:
                for row in self.store.pending():
                    if row["state"] == "conflict" and not retry_conflicts:
                        self.last_error = row["error"]
                        return
                    active_row = row
                    result = self.remote("sale", json.loads(row["payload"]))
                    if result.get("status") != "accepted" or result.get("operation_id") != row["id"]:
                        raise RemoteError("No llegó una confirmación válida. Se conserva el pendiente.", 502)
                    self.store.mark(row["id"], "accepted", result)
                    active_row = None
                self.online, self.last_error = True, ""
                if catalog:
                    self.refresh_catalog()
                    self.store.set("blocked", None)
            except RemoteError as exc:
                self.last_error, self.online = str(exc), False
                if exc.status not in {408, 429, 500, 502, 503, 504}:
                    if active_row is not None:
                        self.store.mark(active_row["id"], "conflict", error=str(exc))
                    # Un rechazo de permisos NO es un corte de internet.
                    self.store.set("blocked", str(exc))

    def checkout(self, data):
        with self.lock:
            if not self.unlocked:
                raise ProtocolError("Desbloquea tu sesión local.")
            operation_id = str(UUID(str(data.get("operation_id"))))
            old = self.store.operation(operation_id)
            if old:
                payload = json.loads(old["payload"])
                saved_items = [{"id": item["id"], "quantity": item["quantity"]} for item in payload["items"]]
                if (saved_items != data.get("items") or amount(payload["cash_received"]) != amount(data.get("cash_received"))
                        or payload["session_id"] != (self.store.get("session") or {}).get("session_id")):
                    raise ProtocolError("Esa referencia ya corresponde a otra venta; revisa el historial antes de volver a cobrar.")
                return self.public_operation(old)
            session = self.store.get("session")
            if not session or not self.store.get("ready"):
                raise ProtocolError("Primero inicia sesión y descarga el respaldo completo.")
            if self.store.get("blocked") or any(row["state"] == "conflict" for row in self.store.pending()):
                raise ProtocolError(self.store.get("blocked") or "Hay una operación que necesita revisión. No se borró ni se duplicó.")
            now = datetime.now(timezone.utc)
            previous_clock = self.store.get("last_clock", 0)
            if now.timestamp() < previous_clock - 120 or now >= datetime.fromisoformat(session["expires_at"]):
                raise ProtocolError("La autorización venció o el reloj retrocedió. Sincroniza y finaliza la sesión.")
            self.store.set("last_clock", max(previous_clock, now.timestamp()))
            products = self.store.products()
            details, total = price_cart(data.get("items"), products)
            if data.get("expected_total") is not None and amount(data["expected_total"]) != total:
                raise ProtocolError("El catálogo cambió el total. Revisa el nuevo valor antes de cobrar.")
            received = amount(data.get("cash_received"))
            if received < total:
                raise ProtocolError("El efectivo recibido no cubre el total.")
            items = [{"id": item["id"], "quantity": item["quantity"], "quote": products[str(item["id"])]["quote"]} for item in data["items"]]
            payload = {"protocol": PROTOCOL, "operation_id": operation_id, "session_id": session["session_id"],
                       "sequence": self.store.last_sequence(session["session_id"]) + 1,
                       "occurred_at": now.isoformat(), "cash_received": str(received), "items": items}
            lines = ["NOVA POS · COMPROBANTE LOCAL", "Referencia: " + operation_id, session["branch"], session["point"],
                     "Cajero: " + session["user"], now.isoformat(), ""]
            for item in details:
                lines.extend([item["producto"], f"  {item['cantidad']} x {item['precio_unitario']} = {item['subtotal']}"])
            lines.extend(["", f"TOTAL: $ {total}", f"RECIBIDO: $ {received}", f"CAMBIO: $ {received-total}", "", "Conserva esta referencia para consultar la venta."])
            self.store.enqueue(payload, "\n".join(lines))
            # Diario de intención duradero antes de la red. La primera confirmación
            # se intenta en la nube; si falla el transporte se conserva localmente.
            self.synchronize(catalog=False)
            row = self.store.operation(operation_id)
            if row["state"] != "conflict":
                try:
                    self.printer.enqueue(operation_id, automatic=True)
                except Exception:
                    # La venta ya está guardada. Un error de papel/configuración
                    # no debe hacer creer al cajero que tiene que volver a cobrar.
                    result = self.public_operation(row)
                    result["printing"] = {"state":"failed", "message":"La venta se guardó, pero no se preparó la impresión. Usa el historial; no repitas la venta."}
                    return result
            return self.public_operation(row)

    def public_operation(self, row):
        result = json.loads(row["result"])
        jobs = self.store.print_jobs(operation_id=row["id"])
        return {"id": row["id"], "state": row["state"], "error": row["error"], "sale_id": result.get("sale_id"),
                "receipt": result.get("receipt_text") or row["receipt"],
                "printing": self.printer.public(jobs[-1]) if jobs else {"state":"not_requested"}}

    def release(self):
        with self.lock:
            if not self.unlocked:
                raise ProtocolError("Desbloquea tu sesión primero.")
            session = self.store.get("session")
            if not session:
                raise ProtocolError("No hay una sesión por finalizar.")
            self.synchronize(catalog=False)
            if self.store.pending():
                raise ProtocolError("Quedan operaciones pendientes. No se puede finalizar la sesión ni cerrar caja.")
            result = self.remote("release", {"session_id": session["session_id"], "sequence": self.store.last_sequence(session["session_id"])})
            if result.get("status") != "released":
                raise ProtocolError("El servidor no confirmó la finalización.")
            self.store.backup()
            self.store.set("session", None)
            self.store.set("ready", False)
            self.store.set("blocked", None)
            self.unlocked = False
            return self.status()
