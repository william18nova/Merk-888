"""Django completo con datos FICTICIOS en un PostgreSQL temporal.

No importa settings de producción. No reutiliza ningún clúster ni dato anterior.
--test ejecuta las pruebas y apaga PostgreSQL; --serve permite inspección manual.
Las pruebas admiten Windows y Linux/WSL. La demostración --serve requiere
Linux/WSL. Usa PostgreSQL y las dependencias de requirements.txt instaladas.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
import signal
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]


def seed():
    from decimal import Decimal
    from datetime import timedelta
    from django.conf import settings
    from django.utils import timezone
    from mainApp.models import (Categoria, Cliente, ConfiguracionFuncionalidad, Empleado,
                                Inventario, MetodoPago, Producto, Proveedor, PuntosPago,
                                Rol, Sucursal, TurnoCaja, Usuario, ConceptoEgreso, Egreso,
                                TurnoEmpleado, Venta, DetalleVenta, PagoVenta)
    from mainApp.permissions import grant_all_permissions_to_web_master
    from mainApp.services.feature_flags import clear_feature_cache
    from local_pos.models import LocalNode
    role = Rol.objects.create(nombre="Web Master")
    user = Usuario.objects.create_user("laboratorio", "prueba-local-2026", rolid=role)
    branch = Sucursal.objects.create(nombre="SUCURSAL FICTICIA - NO REAL")
    point = PuntosPago.objects.create(nombre="CAJA FICTICIA", sucursalid=branch)
    employee = Empleado.objects.create(usuarioid=user, sucursalid=branch, nombre="Usuario", apellido="de prueba",
                            telefono="000", email="local@example.test", numerodocumento="LAB-LOCAL-1")
    TurnoCaja.objects.create(puntopago=point, cajero=user)
    category = Categoria.objects.create(nombre="PRODUCTOS FICTICIOS")
    for name, price, quantity, barcode in (("TOMATE FICTICIO X GR", "3.80", -500, "7700000000001"),
                                           ("ARROZ FICTICIO", "2500", 20, "7700000000002")):
        product = Producto.objects.create(nombre=name, precio=Decimal(price), categoria=category, codigo_de_barras=barcode)
        Inventario.objects.create(sucursalid=branch, productoid=product, cantidad=quantity)
    Cliente.objects.create(nombre="CLIENTE", apellido="FICTICIO", numerodocumento="LAB-C1")
    Proveedor.objects.create(nombre="PROVEEDOR FICTICIO")
    MetodoPago.objects.create(codigo="efectivo", nombre="Efectivo", activo=True, es_efectivo=True)
    MetodoPago.objects.create(codigo="nequi", nombre="Nequi", activo=True, aplica_4xmil_egresos=True)
    MetodoPago.objects.create(codigo="tarjeta", nombre="Tarjeta / Banco Caja Social", activo=True, aplica_4xmil_egresos=True)
    now = timezone.localtime()
    concept = ConceptoEgreso.objects.create(nombre="PAGO FICTICIO DE LABORATORIO", creado_por=user)
    Egreso.objects.create(concepto=concept, monto=Decimal("1000"), medio_pago="efectivo",
                          registrado_por=user, registrado_por_nombre="Usuario de prueba")
    start = now.replace(hour=7, minute=0, second=0, microsecond=0)
    TurnoEmpleado.objects.create(empleado=employee, sucursal=branch, inicio=start,
                                 fin=start + timedelta(hours=7), notas="HORARIO FICTICIO", creado_por=user)
    sale = Venta.objects.create(fecha=now.date(), hora=now.time(), empleadoid=employee,
                                sucursalid=branch, puntopagoid=point, total=Decimal("2500"), mediopago="efectivo")
    DetalleVenta.objects.create(ventaid=sale, productoid=product, cantidad=1, preciounitario=Decimal("2500"))
    PagoVenta.objects.create(ventaid=sale, medio_pago="efectivo", monto=Decimal("2500"))
    for key, enabled in (("ventas_exigir_turno_caja", True), ("nequi_api_recepcion", False),
                         ("telegram_bot_inteligente", False), ("pos_hibrido_piloto", False)):
        ConfiguracionFuncionalidad.objects.create(clave=key, habilitada=enabled)
    clear_feature_cache()
    grant_all_permissions_to_web_master(role.pk)
    LocalNode.objects.create(id=settings.LOCAL_CONFIG["instance_id"])


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--test", action="store_true")
    mode.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8903)
    parser.add_argument("--pg-port", type=int, default=55437)
    parser.add_argument("--pg-bin", default=None)
    parser.add_argument("--replica-tests", action="store_true")
    parser.add_argument("--replica-demo", action="store_true")
    parser.add_argument("--sale-demo", action="store_true")
    parser.add_argument("--sales-tests", action="store_true")
    parser.add_argument("--business-tests", action="store_true")
    parser.add_argument("--business-demo", action="store_true")
    parser.add_argument("--review-demo", action="store_true", help="Dos movimientos FICTICIOS para probar la pantalla de revisión")
    parser.add_argument("--source-port", type=int, default=8905)
    args = parser.parse_args()
    if args.review_demo:
        args.business_demo = True
    if args.business_tests:
        args.sales_tests = True
    if args.business_demo:
        args.sale_demo = True
    if args.sale_demo:
        args.replica_demo = True
    if args.sales_tests:
        args.replica_tests = True
    if os.name == "posix" and os.geteuid() == 0:
        parser.error("Usa un usuario sin privilegios, no root.")
    if os.name == "nt" and args.serve:
        parser.error("La demostración --serve aún requiere Linux/WSL; en Windows usa --test o el runtime instalado.")
    if args.pg_bin is None:
        if os.name == "nt":
            parser.error("Indica --pg-bin con la instalación autorizada de PostgreSQL.")
        args.pg_bin = "/usr/lib/postgresql/16/bin"
    if not Path(args.pg_bin).is_absolute():
        parser.error("--pg-bin debe ser una ruta absoluta.")
    ports = (args.port, args.pg_port, args.source_port) if args.replica_demo else (args.port, args.pg_port)
    if len(set(ports)) != len(ports) or not all(1024 <= port <= 65535 for port in ports):
        parser.error("Puertos inválidos.")
    if args.replica_demo and not args.serve:
        parser.error("La demostración de réplica requiere --serve.")
    for port in ports:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                parser.error(f"El puerto {port} ya está ocupado; no se reutilizará.")
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="nova-full-local-"))
    sys.path.insert(0, str(REPO))
    from local_pos.runtime import private_dir
    private_dir(root)
    config = {"application": "nova-full-local-development-v1", "mode": "development_readonly",
              "instance_id": str(uuid4()), "secret_key": secrets.token_hex(48),
              "database": "nova_full_local_lab", "user": "nova_full_local_lab",
              "password": secrets.token_hex(32), "port": args.pg_port, "data_dir": str(root)}
    config_path = root / "local.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    password_file = root / "init-password"
    password_file.write_text(config["password"], encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k not in {"DATABASE_URL", "DJANGO_SETTINGS_MODULE", "NOVA_LOCAL_CONFIG"}}
    env["PGPASSWORD"] = config["password"]

    def pg(name, *params, check=True):
        executable = name + (".exe" if os.name == "nt" else "")
        return subprocess.run([str(Path(args.pg_bin) / executable), *map(str, params)], env=env,
                              stdin=subprocess.DEVNULL, check=check)

    started = False
    source = None
    worker = None
    stop_worker = threading.Event()
    try:
        pg("initdb", "-D", root / "postgres", "-U", config["user"], "-A", "scram-sha-256",
           "-E", "UTF8", "--locale=C", f"--pwfile={password_file}")
        password_file.unlink()
        socket_option = f' -k "{root}"' if os.name == "posix" else ""
        pg("pg_ctl", "-D", root / "postgres", "-l", root / "postgres.log", "-o",
           f"-h 127.0.0.1 -p {args.pg_port}{socket_option} -c max_connections=30", "-w", "-t", "30", "start")
        started = True
        pg("createdb", "-h", "127.0.0.1", "-p", args.pg_port, "-U", config["user"], config["database"])
        os.environ["NOVA_LOCAL_CONFIG"] = str(config_path)
        os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.hybrid_local_settings"
        from django.conf import settings
        if args.sale_demo:
            settings.HYBRID_LOCAL_SALES_ENABLED = True
            settings.LOCAL_SALES_DEMO_CONTROLS = True
        if args.business_demo:
            settings.HYBRID_LOCAL_OPERATIONS_ENABLED = True
        import django
        if args.replica_tests:
            from copy import deepcopy
            from django.conf import settings
            settings.DATABASES["replica"] = deepcopy(settings.DATABASES["default"])
            settings.DATABASES["replica"]["NAME"] = "nova_full_local_replica_lab"
            settings.DATABASES["replica"]["TEST"] = {"NAME": "test_nova_full_local_replica_lab"}
            if args.sales_tests:
                settings.DATABASES["replica2"] = deepcopy(settings.DATABASES["replica"])
                settings.DATABASES["replica2"]["NAME"] = "nova_full_local_replica2_lab"
                settings.DATABASES["replica2"]["TEST"] = {"NAME": "test_nova_full_local_replica2_lab"}
        django.setup()
        assert "NovaSoft.settings" not in sys.modules, "Se importó configuración de producción"
        from django.core.management import call_command
        call_command("check")
        if args.test:
            labels = ["local_pos.tests", "local_pos.test_replica"] if args.replica_tests else ["local_pos.tests"]
            if args.sales_tests:
                labels.append("local_pos.test_sales")
                labels.append("local_pos.test_sync_review")
            if args.business_tests:
                labels.append("local_pos.test_business")
            labels.append("local_pos.test_lab_runner")
            call_command("test", *labels, interactive=False, verbosity=2,
                         testrunner="local_pos.test_runner.LocalLabRunner")
        else:
            call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
            if args.replica_demo:
                # El receptor inicia vacío: no mezcla los fixtures del origen
                # con ventas, pagos o inventarios creados en otro laboratorio.
                from mainApp.models import Usuario, Rol
                from mainApp.permissions import grant_all_permissions_to_web_master
                from local_pos.models import LocalNode
                role = Rol.objects.create(nombre="Web Master")
                user = Usuario.objects.create_user("laboratorio", "prueba-local-2026", rolid=role)
                grant_all_permissions_to_web_master(role.pk)
                LocalNode.objects.create(id=config["instance_id"])
                pg("createdb", "-h", "127.0.0.1", "-p", args.pg_port, "-U", config["user"], "nova_full_local_source_lab")
                with (root / "source.log").open("w") as log:
                    source = subprocess.Popen([sys.executable, "-B", str(REPO / "scripts/full_local_source.py"), str(args.source_port)],
                        env=os.environ.copy(), stdout=log, stderr=log)
                binding = root / "replica-connection.json"
                deadline = time.monotonic() + 30
                while not binding.exists():
                    if source.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("El origen ficticio no inició; revisar source.log del laboratorio.")
                    time.sleep(0.1)
                from local_pos.replica import synchronize
                from local_pos.replica_transport import ReplicaRemote
                if args.sale_demo:
                    from local_pos.sale_views import DemoRemote
                    remote = DemoRemote(json.loads(binding.read_text()), allow_local=True)
                else:
                    remote = ReplicaRemote(json.loads(binding.read_text()), allow_local=True)
                synchronize(remote, local_user_id=user.pk)
                if args.sale_demo:
                    from local_pos.sales import refresh_authorization, sync_cycle
                    refresh_authorization(remote, local_user_id=user.pk)
                if args.review_demo:
                    from scripts.full_local_sync_fixture import seed_review
                    seed_review(user, remote)
                def refresh():
                    from django.db import connections
                    delay = 30
                    while not stop_worker.wait(delay):
                        try:
                            if args.sale_demo:
                                sync_cycle(remote, local_user_id=user.pk)
                            else:
                                synchronize(remote, local_user_id=user.pk)
                            delay = 30
                        except Exception:
                            delay = min(delay * 2, 300)
                        finally:
                            connections.close_all()
                worker = threading.Thread(target=refresh, daemon=True)
                worker.start()
                print("Réplica inicial lista. Origen y receptor ficticios separados; actualización cada 30 segundos.", flush=True)
            else:
                seed()
            call_command("collectstatic", interactive=False, verbosity=0)
            from scripts.build_full_local_assets import build
            build(root / "staticfiles")
            from django.conf import settings
            settings.LOCAL_ASSET_MAP = json.loads((root / "staticfiles/local_vendor/manifest.json").read_text())["urls"]
            from django.core.wsgi import get_wsgi_application
            from whitenoise import WhiteNoise
            from wsgiref.simple_server import make_server
            application = WhiteNoise(get_wsgi_application(), root=str(root / "staticfiles"), prefix="static/")
            print(f"PRUEBAS FICTICIAS: http://127.0.0.1:{args.port}/local/estado/", flush=True)
            print("Usuario: laboratorio | Contraseña de prueba: prueba-local-2026", flush=True)
            with make_server("127.0.0.1", args.port, application) as server:
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    print("Cerrando el laboratorio ficticio.", flush=True)
    finally:
        stop_worker.set()
        if worker:
            worker.join(timeout=35)
        if source and source.poll() is None:
            source.send_signal(signal.SIGINT)
            try:
                source.wait(timeout=5)
            except subprocess.TimeoutExpired:
                source.terminate()
                source.wait(timeout=5)
        if started:
            pg("pg_ctl", "-D", root / "postgres", "-m", "fast", "-w", "-t", "30", "stop", check=False)
        password_file.unlink(missing_ok=True)
        print(f"Laboratorio ficticio conservado en {root}", flush=True)


if __name__ == "__main__":
    main()
