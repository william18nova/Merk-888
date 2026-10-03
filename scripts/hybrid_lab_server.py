"""Servidor persistente de DATOS FICTICIOS, exclusivo del laboratorio WSL.

No importa NovaSoft.settings, no acepta DSN ni host externo y no envía mensajes.
El controlador Windows inicia este proceso como novapos-test, nunca como root.
"""
import argparse
from datetime import datetime, timedelta, timezone as utc
from decimal import Decimal
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import socket
from socketserver import ThreadingMixIn
import ssl
import subprocess
import sys
import threading
import time
from uuid import UUID
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server

REPO = Path(__file__).resolve().parents[1]
ROOT = Path("/home/novapos-test/.local/share/novapos-lab")
PG_BIN = Path("/usr/lib/postgresql/16/bin")
PG_PORT = 55434
DB_NAME = "nova_hybrid_lab"
CLOUD_PORT = 8894
MARKER = "nova-pos-local-lab-v1-NOT-PRODUCTION"
USER = "cajero_prueba"
PASSWORD = "PruebasNova2026!"  # Usuario ficticio, solo dentro de esta BD local.
PIN = "pruebas123"


def write_json(file, value):
    temp = file.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    temp.replace(file)


def read_json(file):
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def owned_root():
    if os.geteuid() == 0 or Path.home() != Path("/home/novapos-test"):
        raise RuntimeError("Usa exclusivamente el usuario WSL novapos-test sin privilegios.")
    if ROOT.is_symlink():
        raise RuntimeError("No se permiten enlaces para el directorio del laboratorio.")
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    marker = ROOT / "lab-marker.json"
    if marker.exists():
        if read_json(marker).get("application") != MARKER:
            raise RuntimeError("El directorio no pertenece a este laboratorio.")
    else:
        if any(ROOT.iterdir()):
            raise RuntimeError("No se utilizará un directorio preexistente sin identificar.")
        write_json(marker, {"application": MARKER})
    lock = (ROOT / "lab.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lock


def prepare_postgres():
    config_file = ROOT / "config.json"
    data = ROOT / "postgres"
    if config_file.exists():
        config = read_json(config_file)
        if config.get("application") != MARKER or not config.get("password") or not config.get("secret"):
            raise RuntimeError("Configuración de laboratorio inválida; no se sobrescribió.")
    else:
        if data.exists():
            raise RuntimeError("No se reemplazarán credenciales de un clúster existente.")
        config = {"application": MARKER, "password": secrets.token_hex(32), "secret": secrets.token_hex(48)}
        write_json(config_file, config)
    env = {**os.environ, "PGPASSWORD": config["password"]}
    for name in ("PGSERVICE", "PGSERVICEFILE", "PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGOPTIONS"):
        env.pop(name, None)

    def pg(command, *args, check=True):
        return subprocess.run([str(PG_BIN / command), *map(str, args)], env=env, check=check,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if not (data / "PG_VERSION").exists():
        password_file = ROOT / "init-password.txt"
        password_file.write_text(config["password"], encoding="utf-8")
        try:
            pg("initdb", "-D", data, "-U", DB_NAME, "-A", "scram-sha-256", "-E", "UTF8",
               "--locale=C", f"--pwfile={password_file}")
        finally:
            password_file.unlink(missing_ok=True)
    running = pg("pg_ctl", "-D", data, "status", check=False).returncode == 0
    if not running:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", PG_PORT)) == 0:
                raise RuntimeError("El puerto de PostgreSQL ya está ocupado por otro servicio; no se usará.")
        pg("pg_ctl", "-D", data, "-l", ROOT / "postgres.log", "-o",
           f"-h 127.0.0.1 -p {PG_PORT} -k {ROOT} -c max_connections=30", "-w", "-t", "30", "start")
    try:
        import psycopg2
        with psycopg2.connect(host="127.0.0.1", port=PG_PORT, user=DB_NAME,
                              password=config["password"], dbname="postgres", connect_timeout=5) as db:
            with db.cursor() as cur:
                cur.execute("SHOW data_directory")
                if Path(cur.fetchone()[0]).resolve() != data.resolve():
                    raise RuntimeError("La base encontrada no es la del laboratorio.")
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", [DB_NAME])
                exists = cur.fetchone()
        if not exists:
            pg("createdb", "-h", "127.0.0.1", "-p", PG_PORT, "-U", DB_NAME, DB_NAME)
    except Exception:
        pg("pg_ctl", "-D", data, "-m", "fast", "-w", "stop", check=False)
        raise
    return config, lambda: pg("pg_ctl", "-D", data, "-m", "fast", "-w", "-t", "30", "stop", check=False)


def configure_django(config):
    sys.path.insert(0, str(REPO))
    os.environ.pop("DJANGO_SETTINGS_MODULE", None)
    from django.conf import settings
    settings.configure(
        SECRET_KEY=config["secret"], DEBUG=False, ALLOWED_HOSTS=["127.0.0.1"],
        INSTALLED_APPS=["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
                        "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
                        "dal", "dal_select2", "mainApp"],
        AUTH_USER_MODEL="mainApp.Usuario", ROOT_URLCONF="scripts.hybrid_lab_urls",
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField", TIME_ZONE="America/Bogota", USE_TZ=True,
        DATABASES={"default": {"ENGINE": "django.db.backends.postgresql", "HOST": "127.0.0.1",
            "PORT": PG_PORT, "NAME": DB_NAME, "USER": DB_NAME, "PASSWORD": config["password"],
            "CONN_MAX_AGE": 0, "OPTIONS": {"connect_timeout": 5,
                "options": "-c lock_timeout=5000 -c statement_timeout=15000"}}},
        MIGRATION_MODULES={name: None for name in ("mainApp", "auth", "contenttypes", "admin", "sessions")},
        MIDDLEWARE=[], CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
        EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend", STATIC_URL="/static/", MEDIA_ROOT=str(ROOT / "media"),
        TEMPLATES=[{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True}],
        GEMINI_API_KEY="", GROQ_API_KEY="", TELEGRAM_BOT_TOKEN="", NEQUI_API_KEY="",
        BASE_DIR=REPO,
    )
    import django
    django.setup()
    from django.core.management import call_command
    call_command("migrate", run_syncdb=True, interactive=False, verbosity=0, skip_checks=True)


def fixtures():
    from django.db import transaction
    from django.utils import timezone
    from mainApp.models import (Categoria, ConfiguracionFuncionalidad, Empleado, EquipoHibrido, Inventario,
        MetodoPago, Producto, PuntosPago, Rol, Sucursal, TurnoCaja, Usuario)
    from mainApp.services import hybrid
    from mainApp.services.feature_flags import HYBRID_POS_FEATURE, TURN_REQUIRED_FEATURE, clear_feature_cache
    with transaction.atomic():
        branch, _ = Sucursal.objects.get_or_create(nombre="LABORATORIO - NO REAL")
        point, _ = PuntosPago.objects.get_or_create(nombre="CAJA DE PRUEBAS", sucursalid=branch)
        role, _ = Rol.objects.get_or_create(nombre="Web Master")
        user = Usuario.objects.filter(nombreusuario=USER).first()
        if not user:
            user = Usuario.objects.create_user(USER, PASSWORD, rolid=role)
        Empleado.objects.get_or_create(usuarioid=user, defaults={"sucursalid": branch, "nombre": "Cajero",
            "apellido": "Ficticio", "telefono": "000", "email": "pruebas@example.test", "numerodocumento": "LAB-001"})
        if not TurnoCaja.objects.filter(puntopago=point, cajero=user, estado="ABIERTO").exists():
            TurnoCaja.objects.create(puntopago=point, cajero=user)
        MetodoPago.objects.get_or_create(codigo="efectivo", defaults={"nombre": "Efectivo", "activo": True})
        for flag, active in ((HYBRID_POS_FEATURE, True), (TURN_REQUIRED_FEATURE, True), ("nequi_api", False), ("telegram_bot", False)):
            ConfiguracionFuncionalidad.objects.update_or_create(clave=flag, defaults={"habilitada": active})
        cat, _ = Categoria.objects.get_or_create(nombre="PRODUCTOS FICTICIOS")
        rows = [("TOMATE DE PRUEBA POR GRAMO", "3.80", 0, "7700000000001"),
                ("ARROZ DE PRUEBA", "2500", 20, "7700000000002"),
                ("AGUA DE PRUEBA", "1800", 3, "7700000000003")]
        for name, price, stock, barcode in rows:
            product, _ = Producto.objects.get_or_create(nombre=name, defaults={"precio": Decimal(price), "categoria": cat, "codigo_de_barras": barcode})
            # No restablecer existencias ni precios al volver a abrir el laboratorio.
            Inventario.objects.get_or_create(sucursalid=branch, productoid=product, defaults={"cantidad": stock})
        device = EquipoHibrido.objects.filter(punto=point, activo=True).first()
        if device is None:
            device, code = hybrid.create_device(actor=user, point=point, name="PC LABORATORIO - NO REAL")
        else:
            secret = secrets.token_urlsafe(32)
            device.enlace_hash = hybrid.digest(secret)
            device.enlace_vence = timezone.now() + timedelta(minutes=15)
            device.save(update_fields=["enlace_hash", "enlace_vence"])
            code = f"{device.pk}.{secret}"
    clear_feature_cache()
    return code


def certificate(bridge):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nova POS laboratorio LOCAL")])
    now = datetime.now(utc.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1))
            .not_valid_after(now+timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    keyfile, certfile = ROOT / "tls-key.pem", ROOT / "tls-cert.pem"
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (bridge / "lab-ca.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return certfile, keyfile


def snapshot(bridge, run_id):
    from django.db import connections
    from django.db.models import Sum
    from django.utils import timezone
    from mainApp.models import Venta, Inventario, PuntosPago, OperacionHibrida
    try:
        rows = list(Venta.objects.order_by("-ventaid").values("ventaid", "total", "fecha", "hora")[:20])
        write_json(bridge / "cloud-state.json", {"run_id": run_id, "updated": timezone.now().isoformat(),
            "sales": Venta.objects.count(), "operations": OperacionHibrida.objects.count(),
            "total": str(Venta.objects.aggregate(total=Sum("total"))["total"] or 0),
            "cash": str(PuntosPago.objects.get(nombre="CAJA DE PRUEBAS").dinerocaja),
            "products": [{"id": row.productoid_id, "name": row.productoid.nombre, "stock": row.cantidad,
                          "price": str(row.productoid.precio)} for row in Inventario.objects.select_related("productoid").order_by("productoid_id")],
            "recent": [{key: str(value) for key, value in row.items()} for row in rows]})
    finally:
        connections.close_all()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge", required=True)
    parser.add_argument("--run-id", required=True, type=UUID)
    args = parser.parse_args()
    bridge = Path(args.bridge).resolve()
    if not str(bridge).startswith("/mnt/c/Users/") or tuple(bridge.parts[-4:]) != ("AppData", "Local", "NovaPOS-Lab", "bridge"):
        raise RuntimeError("El intercambio debe estar en AppData/Local/NovaPOS-Lab/bridge.")
    bridge.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    lock = owned_root()
    config, stop_db = prepare_postgres()
    server = None
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    run_id = str(args.run_id)
    control_token = secrets.token_hex(32)
    control = {"online": False, "heartbeat": time.monotonic()}
    try:
        configure_django(config)
        code = fixtures()
        cert, key = certificate(bridge)
        from django.core.wsgi import get_wsgi_application
        from django.db import connections
        application = get_wsgi_application()

        def gate(environ, start_response):
            try:
                if environ.get("PATH_INFO") == "/__lab/control":
                    if environ.get("REQUEST_METHOD") != "POST" or not secrets.compare_digest(
                            environ.get("HTTP_AUTHORIZATION", ""), "Bearer " + control_token):
                        start_response("403 Forbidden", [("Content-Type", "application/json")])
                        return [b'{"ok":false}']
                    try:
                        length = int(environ.get("CONTENT_LENGTH") or 0)
                        if not 0 < length <= 1000:
                            raise ValueError
                        data = json.loads(environ["wsgi.input"].read(length))
                        if data.get("run_id") != run_id:
                            raise ValueError
                        if "online" in data:
                            if type(data["online"]) is not bool:
                                raise ValueError
                            control["online"] = data["online"]
                        if data.get("stop") is True:
                            stop.set()
                        control["heartbeat"] = time.monotonic()
                        start_response("200 OK", [("Content-Type", "application/json")])
                        return [b'{"ok":true}']
                    except (ValueError, TypeError, AttributeError):
                        start_response("400 Bad Request", [("Content-Type", "application/json")])
                        return [b'{"ok":false}']
                if not control["online"]:
                    start_response("503 Service Unavailable", [("Content-Type", "application/json")])
                    return [b'{"ok":false,"error":"Sin conexion SIMULADA. Solo laboratorio."}']
                result = application(environ, start_response)
                try:
                    return [b"".join(result)]
                finally:
                    if hasattr(result, "close"):
                        result.close()
            finally:
                connections.close_all()

        class Server(ThreadingMixIn, WSGIServer):
            daemon_threads = True

        class Handler(WSGIRequestHandler):
            def log_message(self, *_args):
                pass

        server = make_server("127.0.0.1", CLOUD_PORT, gate, Server, Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        snapshot(bridge, run_id)
        write_json(bridge / "ready.json", {"run_id": run_id, "cloud": f"https://127.0.0.1:{CLOUD_PORT}",
            "username": USER, "password": PASSWORD, "pin": PIN, "code": code, "control_token": control_token})
        print("Laboratorio aislado disponible; datos ficticios conservados.", flush=True)
        while not stop.wait(2):
            if time.monotonic() - control["heartbeat"] > 120:
                break
            snapshot(bridge, run_id)
    finally:
        if server:
            server.shutdown()
            server.server_close()
        from django.db import connections
        connections.close_all()
        stop_db()
        lock.close()
        print("Laboratorio detenido. No se borraron ventas ni pendientes.", flush=True)


if __name__ == "__main__":
    main()
