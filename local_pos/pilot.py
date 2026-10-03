"""Laboratorio persistente de dos computadores. SOLO datos ficticios y red privada."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import ssl
import sys

from . import runtime
from .pilot_tls import FORMAT, private_host, validate_link


def make_certificate(root, host):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Nova POS - SOLO PRUEBAS")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=60))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(host))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=True,
                           data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                           encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    with (root / "server-key.pem").open("xb") as stream:
        stream.write(private)
    (root / "server-key.pem").chmod(0o600)
    certificate = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    (root / "server-cert.pem").write_text(certificate, encoding="ascii")
    return certificate, cert.fingerprint(hashes.SHA256()).hex()


def seed_hub():
    from decimal import Decimal
    from django.db import transaction
    from mainApp.models import (Categoria, Cliente, ConfiguracionFuncionalidad, Empleado, Inventario,
                                MetodoPago, Producto, PuntosPago, Rol, Sucursal, TurnoCaja, Usuario)
    from mainApp.services import hybrid
    from mainApp.services.feature_flags import clear_feature_cache
    credentials = {}
    with transaction.atomic():
        role = Rol.objects.get(nombre="Web Master")
        admin = Usuario.objects.get(nombreusuario="servidor_pruebas")
        branch = Sucursal.objects.create(nombre="LABORATORIO FICTICIO - DOS COMPUTADORES")
        for index in (1, 2):
            password = secrets.token_urlsafe(18)
            user = Usuario.objects.create_user(f"prueba{index}", password, rolid=role)
            Empleado.objects.create(usuarioid=user, sucursalid=branch, nombre=f"Cajero {index}",
                apellido="FICTICIO", telefono=f"000{index}", email=f"prueba{index}@example.test", numerodocumento=f"LAB-{index}")
            point = PuntosPago.objects.create(nombre=f"CAJA FICTICIA {index}", sucursalid=branch)
            TurnoCaja.objects.create(puntopago=point, cajero=user)
            device, code = hybrid.create_device(actor=admin, point=point, name=f"COMPUTADOR FICTICIO {index}")
            credentials[str(index)] = {"username": user.nombreusuario, "password": password,
                                       "device_id": str(device.pk), "code": code}
        category = Categoria.objects.create(nombre="SOLO PRODUCTOS FICTICIOS")
        for name, price, quantity, barcode in (("TOMATE FICTICIO X GR", "3.80", -500, "7700000000001"),
                                               ("ARROZ FICTICIO", "2500", 20, "7700000000002")):
            product = Producto.objects.create(nombre=name, precio=Decimal(price), categoria=category, codigo_de_barras=barcode)
            Inventario.objects.create(sucursalid=branch, productoid=product, cantidad=quantity)
        Cliente.objects.create(nombre="CLIENTE", apellido="FICTICIO", numerodocumento="LAB-C1")
        for code, label, cash in (("efectivo", "Efectivo", True), ("nequi", "Nequi", False),
                                  ("tarjeta", "Tarjeta / Banco Caja Social", False)):
            MetodoPago.objects.create(codigo=code, nombre=label, activo=True, es_efectivo=cash,
                                       aplica_4xmil_egresos=not cash)
        for code, enabled in (("ventas_exigir_turno_caja", True), ("nequi_api_recepcion", False),
                              ("telegram_bot_inteligente", False), ("pos_hibrido_piloto", True)):
            ConfiguracionFuncionalidad.objects.update_or_create(clave=code, defaults={"habilitada": enabled})
    clear_feature_cache()
    return credentials


def initialize_hub(root, pg_bin, *, host, port=8940, pg_port=55460):
    host = private_host(host)
    root = Path(root).absolute()
    # initialize refuses populated paths. A hub is never made from real data
    # or from an existing cashier installation.
    runtime.initialize(root, pg_bin, username="servidor_pruebas", password=secrets.token_urlsafe(32),
                       http_port=port, pg_port=pg_port)
    state = runtime.installed_state(root)
    pg = runtime.Postgres(root, state["pg_bin"], runtime.read_json(root / "local.json"))
    with runtime.installation_lock(root):
        pg.start()
        try:
            credentials = seed_hub()
            certificate, fingerprint = make_certificate(root, host)
            link = {"format": FORMAT, "url": f"https://{host}:{port}",
                    "certificate": certificate, "sha256": fingerprint}
            runtime.save_json(root / "conexion-pruebas.json", link)
            runtime.save_json(root / "pilot-hub.json", {"format": FORMAT, "host": host, "port": port,
                               "instance_id": state["instance_id"], "accounts": credentials})
        finally:
            from django.db import connections
            connections.close_all()
            pg.stop()
    print("Servidor FICTICIO creado. Archivo público para copiar a las cajas:", root / "conexion-pruebas.json")
    print("Huella SHA256 (compárala al vincular):", fingerprint)


def hub_settings(root):
    root = Path(root).absolute()
    state = runtime.installed_state(root)
    hub = runtime.read_json(root / "pilot-hub.json")
    if hub.get("format") != FORMAT or hub.get("instance_id") != state["instance_id"]:
        raise RuntimeError("Esta instalación no es un servidor ficticio válido.")
    private_host(hub["host"])
    os.environ["NOVA_LOCAL_CONFIG"] = str(root / "local.json")
    os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.hybrid_local_settings"
    from django.conf import settings
    settings.ROOT_URLCONF = "local_pos.pilot_urls"
    settings.MIDDLEWARE = ["django.middleware.common.CommonMiddleware"]
    settings.ALLOWED_HOSTS = [hub["host"]]
    settings.HYBRID_REPLICA_MIN_INTERVAL = 0  # Only the fictional source.
    import django
    django.setup()
    assert "NovaSoft.settings" not in sys.modules
    return root, state, hub


def print_pairing(root, hub):
    from mainApp.models import EquipoHibrido
    from mainApp.services.hybrid import digest
    from django.utils import timezone as djtime
    changed = False
    for index, account in hub["accounts"].items():
        device = EquipoHibrido.objects.get(pk=account["device_id"])
        if device.token_hash:
            print(f"Computador {index}: ya vinculado; conserva su instalación y sus datos.")
            continue
        if device.enlace_vence <= djtime.now():
            secret = secrets.token_urlsafe(32)
            account["code"] = f"{device.pk}.{secret}"
            device.enlace_hash = digest(secret)
            device.enlace_vence = djtime.now() + timedelta(minutes=15)
            device.save(update_fields=["enlace_hash", "enlace_vence"])
            changed = True
        # Shown in the owner's console only. Never included in distributed ZIPs
        # or served via HTTP. These passwords are generated for this lab alone.
        print(f"\nCAJA {index} - SOLO PRUEBAS\nUsuario: {account['username']}\nContraseña de pruebas: {account['password']}\nCódigo (15 minutos): {account['code']}")
    if changed:
        runtime.save_json(root / "pilot-hub.json", hub)
    print("\nHuella SHA256:", runtime.read_json(root / "conexion-pruebas.json")["sha256"], flush=True)


def serve_hub(root, *, ready_callback=None, display_credentials=True):
    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import WSGIServer, WSGIRequestHandler
    root, state, hub = hub_settings(root)
    pg = runtime.Postgres(root, state["pg_bin"], runtime.read_json(root / "local.json"))
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(root / "server-cert.pem", root / "server-key.pem")

    class Server(ThreadingMixIn, WSGIServer):
        daemon_threads = True

        def get_request(self):
            sock, addr = super().get_request()
            sock.settimeout(10)
            try:
                return tls.wrap_socket(sock, server_side=True), addr
            except Exception:
                sock.close()
                raise

    class Handler(WSGIRequestHandler):
        def get_environ(self):
            env = super().get_environ()
            env["HTTPS"] = "on"
            env["wsgi.url_scheme"] = "https"
            return env

        def log_message(self, *args):
            pass  # No credentials, requests or synthetic customer data in logs.

    with runtime.installation_lock(root):
        pg.start()
        try:
            from django.core.wsgi import get_wsgi_application
            with Server((hub["host"], hub["port"]), Handler) as server:
                server.set_app(get_wsgi_application())
                if display_credentials:
                    print_pairing(root, hub)
                print(f"Servidor FICTICIO: https://{hub['host']}:{hub['port']} - Ctrl+C para detener.", flush=True)
                if ready_callback:
                    ready_callback(server)
                server.serve_forever(poll_interval=.2)
        finally:
            from django.db import connections
            connections.close_all()
            pg.stop()


def connect(root, link_file, *, username, code, password, confirm_fingerprint):
    root = Path(root).absolute()
    state = runtime.installed_state(root)
    if (root / "pilot-hub.json").exists():
        raise RuntimeError("El servidor no es una caja. Inicializa otra carpeta para la caja.")
    file = Path(link_file)
    if file.is_symlink() or file.stat().st_size > 30000:
        raise ValueError("Archivo de conexión inválido.")
    data = json.loads(file.read_text(encoding="utf-8"))
    url = validate_link(data)
    if not secrets.compare_digest(str(confirm_fingerprint).lower(), data["sha256"]):
        raise ValueError("Confirma la huella completa que muestra el servidor de pruebas.")
    path = root / "pilot-trust.json"
    with runtime.installation_lock(root):
        if (root / "replica-connection.json").exists():
            raise RuntimeError("Esta caja ya está vinculada; no se reemplazará.")
        if path.exists() and runtime.read_json(path) != data:
            raise RuntimeError("Ya hay otro servidor asignado. No se mezclan los laboratorios.")
        runtime.save_json(path, data)
    os.environ["NOVA_LOCAL_CONFIG"] = str(root / "local.json")
    runtime.pair(root, url, username=username, code=code, password=password)


def summary(root):
    root, state, hub = hub_settings(root)
    pg = runtime.Postgres(root, state["pg_bin"], runtime.read_json(root / "local.json"))
    @contextmanager
    def database():
        if pg.status():
            yield
        else:
            with runtime.installation_lock(root):
                pg.start()
                try:
                    yield
                finally:
                    from django.db import connections
                    connections.close_all()
                    pg.stop()
    with database():
        from mainApp.models import Inventario, Venta, Egreso, OperacionHibrida, TurnoCaja, ReintegroVenta
        from django.db.models import Count
        result = {"fictitious": True, "sales": Venta.objects.count(), "expenses": Egreso.objects.count(),
                  "returns": ReintegroVenta.objects.count(), "operations": OperacionHibrida.objects.count(),
                  "inventory": list(Inventario.objects.values("productoid__nombre", "cantidad")),
                  "turns": list(TurnoCaja.objects.values("puntopago__nombre", "cajero__nombreusuario", "estado")),
                  "sales_by_author": list(Venta.objects.values("empleadoid__usuarioid__nombreusuario").annotate(count=Count("pk")))}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    sub = parser.add_subparsers(dest="action", required=True)
    init = sub.add_parser("hub-init")
    init.add_argument("--pg-bin", required=True, type=Path)
    init.add_argument("--host", required=True)
    init.add_argument("--port", type=int, default=8940)
    init.add_argument("--pg-port", type=int, default=55460)
    sub.add_parser("hub-serve")
    sub.add_parser("hub-summary")
    bind = sub.add_parser("connect")
    bind.add_argument("--link", required=True, type=Path)
    bind.add_argument("--username", required=True)
    args = parser.parse_args()
    try:
        if args.action == "hub-init":
            initialize_hub(args.data_dir, args.pg_bin, host=args.host, port=args.port, pg_port=args.pg_port)
        elif args.action == "hub-serve":
            serve_hub(args.data_dir)
        elif args.action == "hub-summary":
            summary(args.data_dir)
        else:
            connect(args.data_dir, args.link, username=args.username,
                    confirm_fingerprint=input("Huella SHA256 completa mostrada por el servidor: ").strip(),
                    code=getpass.getpass("Código de vinculación de ESTA caja: ").strip(),
                    password=getpass.getpass("Contraseña del usuario de PRUEBAS (no la local): "))
    except KeyboardInterrupt:
        print("Proceso de pruebas detenido. Los datos están conservados.")
    except Exception as exc:
        print(str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "Prueba no completada. Conserva los archivos y solicita revisión.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
