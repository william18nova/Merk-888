"""Aceptación sin impresora: procesos reales, HTTPS y PostgreSQL desechable.

No altera red/reloj del PC. Los fallos se inyectan solamente en este servidor.
HYBRID_ACCEPTANCE_EXE permite probar el binario distribuido en vez de Python.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone as utc
from decimal import Decimal
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import queue
import socket
from socketserver import ThreadingMixIn
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from unittest import skipUnless
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.core.cache import cache
from django.core.wsgi import get_wsgi_application
from django.db import connection, connections
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.utils import timezone

from hybrid_client.backup import decrypt, restore_for_review
from hybrid_client.client import RemoteError
from hybrid_client.recovery import Recovery
from hybrid_client.store import Store
from .models import (Categoria, ConfiguracionFuncionalidad, Empleado, EquipoHibrido,
    Inventario, MetodoPago, OperacionHibrida, Producto, PuntosPago, Rol, SesionHibrida,
    Sucursal, TurnoCaja, Usuario, Venta)
from .services import hybrid, hybrid_recovery
from .services.feature_flags import HYBRID_POS_FEATURE, TURN_REQUIRED_FEATURE, clear_feature_cache
from .views import TurnoCajaIniciarCierreApi, TurnoCajaCerrarApi

ROOT = Path(__file__).resolve().parents[1]
PIN = "clave-local-solo-pruebas"
PASSWORD = "usuario-ficticio-solo-pruebas"
BACKUP_PASSWORD = "respaldo-ficticio-solo-pruebas"


def wait_json(path, timeout=110):
    deadline = time.monotonic() + timeout
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise AssertionError(f"No llego el resultado de prueba {path.name}")
        time.sleep(.1)
    return json.loads(path.read_text())


def send_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


class ThreadedWSGI(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *_args):
        pass


class FaultGate:
    def __init__(self):
        self.application = get_wsgi_application()
        self.offline = set()
        self.hold_before = self.hold_after = False
        self.lost_ack = False
        self.clock_errors = []
        self.entered, self.resume = threading.Event(), threading.Event()

    def __call__(self, environ, start_response):
        device = environ.get("HTTP_AUTHORIZATION", "").removeprefix("Bearer ").split(".")[0]
        sale = environ["PATH_INFO"].endswith("/sale/")
        try:
            if device in self.offline:
                start_response("503 Service Unavailable", [("Content-Type", "application/json")])
                return [b'{"ok":false,"error":"Corte SIMULADO en servidor de pruebas"}']
            if sale and self.hold_before:
                self.entered.set()
                if not self.resume.wait(15):
                    raise AssertionError("No se libero la barrera de prueba")
            payload = None
            if sale:
                raw = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
                environ["wsgi.input"] = io.BytesIO(raw)
                payload = json.loads(raw)
            status_headers = []
            result = self.application(environ, lambda status, headers, exc_info=None: status_headers.append((status, headers)))
            try:
                body = b"".join(result)
            finally:
                if hasattr(result, "close"):
                    result.close()
            if sale and json.loads(body).get("code") == "clock":
                session = SesionHibrida.objects.get(pk=payload["session_id"])
                self.clock_errors.append({"sale":payload["occurred_at"], "start":session.creada_en.isoformat(),
                    "expiry":session.vence_en.isoformat(), "server":timezone.now().isoformat()})
            if sale and self.hold_after:
                self.entered.set()
                if not self.resume.wait(15):
                    raise AssertionError("No se libero la barrera posterior")
            if sale and self.lost_ack:
                self.lost_ack = False
                start_response("503 Service Unavailable", [("Content-Type", "application/json")])
                return [b'{"ok":false,"error":"ACK perdido SIMULADO"}']
            start_response(*status_headers[0])
            return [body]
        finally:
            connections.close_all()


class RegisterProcess:
    def __init__(self, folder, ca):
        self.folder, self.ca = Path(folder), str(ca)
        self.folder.mkdir()
        self.process = None
        self.log = None
        self.opener = build_opener(ProxyHandler({}))

    def start(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{self.port}"
        command = [os.environ["HYBRID_ACCEPTANCE_EXE"]] if os.environ.get("HYBRID_ACCEPTANCE_EXE") else [sys.executable, "-B", "-m", "hybrid_client"]
        self.log = (self.folder / "process-test.log").open("ab")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        self.process = subprocess.Popen(command + ["--data-dir", str(self.folder), "--port", str(self.port), "--no-browser"],
            cwd=ROOT, env={**os.environ, "SSL_CERT_FILE": self.ca, "NO_PROXY": "127.0.0.1,localhost"},
            stdout=self.log, stderr=self.log, **options)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                html = self.request("/")[1].decode()
                self.token = re.search(r'name="local-token" content="([^"]+)"', html)[1]
                return self
            except OSError:
                if self.process.poll() is not None:
                    break
                time.sleep(.05)
        raise AssertionError("La caja de prueba no arranco; revisar process-test.log")

    def request(self, path, data=None, *, headers=None):
        head = {"Origin": self.url, "Content-Type": "application/json", "X-Local-Token": getattr(self, "token", "")}
        head.update(headers or {})
        req = Request(self.url + path, data=None if data is None else json.dumps(data).encode(), headers=head)
        try:
            response = self.opener.open(req, timeout=12)
        except HTTPError as error:
            response = error
        with response:
            return response.code, response.read()

    def api(self, path, data=None, expected=200):
        status, body = self.request("/api/" + path, data)
        if status != expected:
            raise AssertionError(f"{path}: HTTP {status}, esperado {expected}: {body[:500]!r}")
        return json.loads(body)

    def stop(self, abrupt=False):
        if self.process is not None:
            if self.process.poll() is None:
                (self.process.kill if abrupt else self.process.terminate)()
            self.process.wait(timeout=10)
            self.process = None
        if self.log:
            self.log.close()
            self.log = None

    @property
    def store(self):
        return Store(self.folder)


@skipUnless(connection.vendor == "postgresql", "Aceptacion exige PostgreSQL desechable")
@override_settings(ALLOWED_HOSTS=["127.0.0.1", "testserver"], SECRET_KEY="acceptance-fictitious-only", SECURE_SSL_REDIRECT=False)
class HybridAcceptanceTests(TransactionTestCase):
    def setUp(self):
        self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
        self.assertEqual(connection.settings_dict["NAME"], "test_nova_hybrid_ci")
        cache.clear()
        self.temp = tempfile.TemporaryDirectory(prefix="novapos-acceptance-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.branch = Sucursal.objects.create(nombre="ACEPTACION FICTICIA")
        self.role = Rol.objects.create(nombre="Web Master")
        self.admin = Usuario.objects.create_user("admin-acceptance", PASSWORD, rolid=self.role)
        MetodoPago.objects.create(codigo="efectivo", nombre="Efectivo", activo=True)
        for key in (HYBRID_POS_FEATURE, TURN_REQUIRED_FEATURE):
            ConfiguracionFuncionalidad.objects.create(clave=key, habilitada=True)
        clear_feature_cache()
        category = Categoria.objects.create(nombre="Solo pruebas")
        self.product = Producto.objects.create(nombre="TOMATE DE PRUEBA", precio=Decimal("3.80"), categoria=category, codigo_de_barras="770123")
        self.stock = Inventario.objects.create(sucursalid=self.branch, productoid=self.product, cantidad=0)
        self.gate = FaultGate()
        self.server = make_server("127.0.0.1", 0, self.gate, ThreadedWSGI, QuietHandler)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NovaPOS local acceptance only")])
        now = datetime.now(utc.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(key, hashes.SHA256()))
        self.ca = self.folder / "test-ca.pem"
        self.ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path = self.folder / "test-key.pem"
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.ca, key_path)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.cloud_url = f"https://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_cloud)
        self.registers = []
        self.addCleanup(self.stop_registers)

    def stop_cloud(self):
        self.gate.resume.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def stop_registers(self):
        for reg in self.registers:
            reg.stop()

    def register(self):
        number = len(self.registers) + 1
        point = PuntosPago.objects.create(nombre=f"Caja ficticia {number}", sucursalid=self.branch)
        user = Usuario.objects.create_user(f"cashier-{number}", PASSWORD, rolid=self.role)
        Empleado.objects.create(usuarioid=user, sucursalid=self.branch, nombre="Ficticio", apellido=str(number), telefono=str(number), email=f"{number}@example.test", numerodocumento=str(number))
        turn = TurnoCaja.objects.create(puntopago=point, cajero=user)
        device, code = hybrid.create_device(actor=self.admin, point=point, name=f"PC ficticio {number}")
        reg = RegisterProcess(self.folder / f"register-{number}", self.ca)
        self.registers.append(reg)
        reg.start()
        reg.api("enroll", {"url": self.cloud_url, "code": code})
        status = reg.api("start", {"username": user.nombreusuario, "password": PASSWORD, "pin": PIN})
        self.assertTrue(status["ready"])
        reg.session_id, reg.device_id = status["session"]["session_id"], str(device.pk)
        reg.turn, reg.point, reg.user = turn, point, user
        return reg

    def sale(self, reg, quantity=500, **kw):
        data = {"operation_id": str(uuid4()), "session_id": reg.session_id,
                "items": [{"id": self.product.pk, "quantity": quantity}], "cash_received": "100000"}
        data.update(kw)
        return data

    def assert_totals(self, quantity, count):
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.cantidad, -quantity)
        self.assertEqual(Venta.objects.count(), count)
        self.assertEqual(OperacionHibrida.objects.count(), count)
        for operation in OperacionHibrida.objects.select_related("venta"):
            self.assertEqual(operation.venta.fecha, timezone.localtime(operation.ocurrida_en).date())
        for reg in self.registers:
            with reg.store.connect() as db:
                self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(db.execute("SELECT COUNT(*) FROM print_jobs").fetchone()[0], 0)

    def test_two_processes_offline_restart_automatic_sync_and_real_close(self):
        first, second = self.register(), self.register()
        self.assertEqual(first.api("products")[0]["stock"], second.api("products")[0]["stock"])
        self.gate.offline.update([first.device_id, second.device_id])
        def sell_batch(reg):
            for _ in range(15):
                self.assertEqual(reg.api("checkout", self.sale(reg))["state"], "pending")
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(sell_batch, [first, second]))
        self.assertFalse(Venta.objects.exists())
        first.api("release", {"session_id": first.session_id}, expected=400)
        request = RequestFactory().post("/", {"turno_id": first.turn.pk}); request.user = first.user
        self.assertEqual(TurnoCajaIniciarCierreApi.as_view()(request).status_code, 409)
        first.stop(abrupt=True); second.stop(abrupt=True)
        first.start(); second.start()
        for reg in (first, second):
            self.assertFalse(reg.api("status")["unlocked"])
            reg.api("history", expected=401)
            reg.api("unlock", {"pin": PIN})
            self.assertEqual(reg.api("status")["pending"], 15)
        self.gate.offline.clear()
        # No /sync manual: comprobar el worker real de 20 segundos.
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if all(reg.api("status")["pending"] == 0 for reg in (first, second)):
                break
            time.sleep(.2)
        for reg in (first, second):
            self.assertEqual(reg.api("status")["pending"], 0)
            reg.point.refresh_from_db(); self.assertEqual(reg.point.dinerocaja, Decimal("28500"))
            reg.api("release", {"session_id": reg.session_id})
            self.assertTrue((reg.folder / "backup.sqlite3").is_file())
            request = RequestFactory().post("/", {"turno_id": reg.turn.pk}); request.user = reg.user
            self.assertEqual(TurnoCajaIniciarCierreApi.as_view()(request).status_code, 200)
            request = RequestFactory().post("/", {"turno_id": reg.turn.pk, "efectivo_entregado": "28500", "facturas_pagadas": "0", "medios_json": "[]", "ptm_transacciones": "0"}); request.user = reg.user
            response = TurnoCajaCerrarApi.as_view()(request)
            self.assertEqual(response.status_code, 200, response.content)
            reg.turn.refresh_from_db()
            self.assertEqual(reg.turn.estado, "CERRADO")
            self.assertEqual(reg.turn.ventas_total, Decimal("28500"))
            self.assertEqual(reg.turn.diferencia_total, 0)
        self.assert_totals(15000, 30)

    def test_crash_during_request_and_after_commit_preserves_one_sale(self):
        reg = self.register()
        for phase in ("hold_before", "hold_after"):
            with self.subTest(phase=phase):
                self.gate.entered.clear(); self.gate.resume.clear()
                setattr(self.gate, phase, True)
                data = self.sale(reg)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    attempt = pool.submit(reg.api, "checkout", data)
                    self.assertTrue(self.gate.entered.wait(8))
                    self.assertEqual(reg.store.operation(data["operation_id"])["state"], "pending")
                    reg.stop(abrupt=True)
                    self.gate.resume.set()
                    try: attempt.result(timeout=10)
                    except (OSError, ValueError): pass
                setattr(self.gate, phase, False)
                reg.start(); reg.api("unlock", {"pin": PIN})
                reg.api("sync", {})
                self.assertEqual(reg.api("checkout", data)["state"], "accepted")
                reg.api("sync", {})
        self.assert_totals(1000, 2)
        self.assertFalse(reg.store.pending())

    def test_lost_ack_duplicate_click_and_changed_payload(self):
        reg = self.register()
        self.gate.lost_ack = True
        data = self.sale(reg)
        self.assertEqual(reg.api("checkout", data)["state"], "pending")
        self.assertEqual(Venta.objects.count(), 1)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: reg.api("checkout", data), range(4)))
        self.assertEqual({row["id"] for row in results}, {data["operation_id"]})
        reg.api("sync", {})
        reg.api("checkout", {**data, "cash_received": "110000"}, expected=400)
        self.assert_totals(500, 1)

    def test_catalog_changes_and_conflicts_do_not_drop_pending(self):
        reg = self.register()
        self.gate.offline.add(reg.device_id)
        first = reg.api("checkout", self.sale(reg))
        Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("4"))
        self.gate.offline.clear(); reg.api("sync", {})
        self.assertEqual(OperacionHibrida.objects.get(pk=first["id"]).respuesta["total"], "1900")
        self.assertEqual(reg.api("products")[0]["price"], "4.00")
        reg.api("checkout", self.sale(reg, expected_total="1900"), expected=400)
        self.gate.offline.add(reg.device_id)
        pending = reg.api("checkout", self.sale(reg))
        Producto.objects.filter(pk=self.product.pk).update(tipo_ptm="retiro")
        self.gate.offline.clear(); status = reg.api("sync", {})
        self.assertEqual(status["conflicts"], 1)
        reg.api("checkout", self.sale(reg), expected=400)
        self.assertEqual(Venta.objects.count(), 1)
        Producto.objects.filter(pk=self.product.pk).update(tipo_ptm=None)
        self.assertEqual(reg.api("sync", {})["pending"], 0)
        self.assertEqual(OperacionHibrida.objects.get(pk=pending["id"]).respuesta["total"], "2000")
        self.assert_totals(1000, 2)

    def test_revoked_user_and_expired_local_lease_cannot_continue_selling(self):
        reg = self.register()
        Usuario.objects.filter(pk=reg.user.pk).update(is_active=False)
        status = reg.api("sync", {})
        self.assertTrue(status["error"])
        reg.api("checkout", self.sale(reg), expected=400)
        Usuario.objects.filter(pk=reg.user.pk).update(is_active=True)
        self.assertEqual(reg.api("sync", {})["error"], "")
        self.gate.offline.add(reg.device_id)
        reg.api("checkout", self.sale(reg))
        reg.stop(abrupt=True)
        session = reg.store.get("session")
        SesionHibrida.objects.filter(pk=reg.session_id).update(vence_en=timezone.now())
        session["expires_at"] = (timezone.now()-timedelta(seconds=1)).isoformat()
        reg.store.set("session", session)
        reg.start(); reg.api("unlock", {"pin": PIN})
        reg.api("checkout", self.sale(reg), expected=400)
        self.gate.offline.clear()
        reg.api("release", {"session_id": reg.session_id})
        self.assert_totals(500, 1)

    def test_encrypted_recovery_over_https_requires_approval_and_new_login(self):
        reg = self.register()
        self.gate.offline.add(reg.device_id)
        sale = reg.api("checkout", self.sale(reg))
        status, encrypted = reg.request("/api/backup", {"session_id":reg.session_id, "pin":PIN,
            "password":BACKUP_PASSWORD, "confirmation":BACKUP_PASSWORD})
        self.assertEqual(status, 200)
        document = decrypt(encrypted, BACKUP_PASSWORD)
        reg.stop(abrupt=True)
        self.gate.offline.clear()
        review = self.folder / "review"
        restore_for_review(document, review)
        record, code = hybrid_recovery.authorize(actor=self.admin, device_id=reg.device_id,
            reason="Recuperacion de equipo ficticio", isolated=True)
        with patch.dict(os.environ, {"SSL_CERT_FILE":str(self.ca), "NO_PROXY":"127.0.0.1"}):
            recovery = Recovery(review, self.cloud_url)
            self.assertEqual(recovery.prepare(code)["state"], "review")
            self.assertEqual(recovery.finish()["state"], "review")
            self.assertFalse(Venta.objects.exists())
            hybrid_recovery.approve(actor=self.admin, recovery_id=record.pk, understood=True)
            result = recovery.finish()
            self.assertEqual(Recovery(review, self.cloud_url).finish()["directory"], result["directory"])
        restored = Store(result["directory"])
        self.assertFalse(restored.get("session")); self.assertFalse(restored.get("pin"))
        self.assertEqual(restored.operation(sale["id"])["state"], "accepted")
        self.assertEqual(reg.store.operation(sale["id"])["state"], "pending")
        reg.start(); reg.api("unlock", {"pin": PIN})
        self.assertEqual(reg.api("sync", {})["conflicts"], 1)
        reg.api("checkout", self.sale(reg), expected=400)
        self.assert_totals(500, 1)

    @skipUnless(os.environ.get("HYBRID_ACCEPTANCE_NODE") or os.environ.get("HYBRID_ACCEPTANCE_BROWSER_BRIDGE"), "Requiere Node/Playwright para navegador real")
    def test_browser_two_registers_three_tabs_and_no_printing(self):
        first, second = self.register(), self.register()
        self.gate.offline.update([first.device_id, second.device_id])
        script = ROOT / "scripts/test_hybrid_acceptance_browser.cjs"
        output = ROOT / "outputs/hybrid-acceptance"
        node = os.environ.get("HYBRID_ACCEPTANCE_NODE", "node.exe")
        def native(path):
            if node.endswith(".exe") and sys.platform.startswith("linux"):
                return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
            return str(path)
        options = {"first":first.url, "second":second.url, "product":str(self.product.pk), "output":native(output),
                   "playwright":os.environ.get("HYBRID_ACCEPTANCE_PLAYWRIGHT", "playwright")}
        bridge = os.environ.get("HYBRID_ACCEPTANCE_BROWSER_BRIDGE")
        if bridge:
            bridge = Path(bridge)
            send_json(bridge / "request.json", options)
            result = wait_json(bridge / "response.json")
            self.assertEqual(result["code"], 0, result)
            print(result["stdout"].strip())
        else:
            command = [node, native(script)]
            for key, value in options.items(): command.extend(["--"+key, value])
            result = subprocess.run(command, capture_output=True, text=True, timeout=100)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            print(result.stdout.strip())
        self.assertEqual(first.api("status")["pending"], 2)
        self.assertEqual(second.api("status")["pending"], 1)
        backup = decrypt((output / "aceptacion.novabackup").read_bytes(), BACKUP_PASSWORD)
        self.assertEqual(len(backup["tables"]["operations"]), 2)
        self.gate.offline.clear()
        first.api("sync", {}); second.api("sync", {})
        self.assert_totals(1500, 3)

    @skipUnless(os.environ.get("HYBRID_ACCEPTANCE_NODE", "").endswith(".exe") or os.environ.get("HYBRID_ACCEPTANCE_WINDOWS_BRIDGE"), "Requiere Windows Node y paquete Windows")
    def test_windows_binaries_two_processes_offline_restart_and_auto_sync(self):
        def native(path):
            if sys.platform == "win32":
                return str(path)
            return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()
        config = {"cloud":self.cloud_url, "ca":self.ca.read_text(), "pin":PIN, "password":PASSWORD,
            "product":self.product.pk, "executable":os.environ.get("HYBRID_ACCEPTANCE_WINDOWS_EXE") or native(ROOT/"dist/hybrid/NovaPOS/NovaPOS.exe"), "registers":[]}
        devices, points = [], []
        for number in range(2):
            point = PuntosPago.objects.create(nombre=f"Windows {number}", sucursalid=self.branch)
            user = Usuario.objects.create_user(f"windows-{number}", PASSWORD, rolid=self.role)
            Empleado.objects.create(usuarioid=user, sucursalid=self.branch, nombre="Ficticio", apellido=str(number), telefono=str(number), email=f"win{number}@example.test", numerodocumento=str(number))
            TurnoCaja.objects.create(puntopago=point, cajero=user)
            device, code = hybrid.create_device(actor=self.admin, point=point, name=f"Windows ficticio {number}")
            devices.append(str(device.pk)); points.append(point)
            config["registers"].append({"code":code, "username":user.nombreusuario})
        bridge = os.environ.get("HYBRID_ACCEPTANCE_WINDOWS_BRIDGE")
        process = None if bridge else subprocess.Popen([os.environ["HYBRID_ACCEPTANCE_NODE"], native(ROOT/"scripts/test_hybrid_windows_processes.cjs")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        positions = {"in":0, "out":0}
        def send(value):
            if bridge:
                send_json(Path(bridge) / f"input-{positions['in']}.json", value)
                positions["in"] += 1
            else:
                process.stdin.write(json.dumps(value)+"\n"); process.stdin.flush()
        received = queue.Queue()
        def reader():
            for line in process.stdout:
                received.put(line)
        if process: threading.Thread(target=reader, daemon=True).start()
        def receive(phase):
            if bridge:
                value = wait_json(Path(bridge) / f"output-{positions['out']}.json", 65)
                positions["out"] += 1
            else:
                value = json.loads(received.get(timeout=65))
            self.assertEqual(value.get("phase"), phase, {"result":value, "clock":self.gate.clock_errors})
            return value
        try:
            send(config); receive("ready")
            self.gate.offline.update(devices); send({"phase":"offline"})
            self.assertEqual(receive("queued")["pending"], [12,12])
            self.assertFalse(Venta.objects.exists())
            self.gate.offline.clear(); send({"phase":"online"})
            self.assertEqual(receive("completed")["sales"], 24)
            if process: self.assertEqual(process.wait(timeout=15), 0, process.stderr.read())
            self.assert_totals(12000, 24)
            self.assertFalse(SesionHibrida.objects.filter(liberada_en__isnull=True).exists())
            for point in points:
                point.refresh_from_db(); self.assertEqual(point.dinerocaja, Decimal("22800"))
        finally:
            if bridge:
                # Liberar el helper si una asercion fallo mientras esperaba
                # la siguiente fase; sus procesos locales se cierran en finally.
                send({"phase":"abort"})
            if process: process.stdin.close()
            if process and process.poll() is None:
                # El helper cierra las cajas en finally cuando se cierra stdin.
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate(); process.wait(timeout=10)
            if process:
                process.stdout.close(); process.stderr.close()

