"""Exercise manage.main/runserver with two isolated PostgreSQL PROCESSES.

Creates only fictional data. The test substitutes a safe settings module in
the CHILD interpreter before manage.py imports it. Production settings are
never read, and normal runtime code contains no test-profile escape hatch.
Clusters and logs are kept, stopped, in the printed temporary directory.
"""
import argparse
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib import request, error, parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {"script", "img"}:
            url = attrs.get("src", "")
        elif tag == "link" and attrs.get("rel") in {"stylesheet", "icon"}:
            url = attrs.get("href", "")
        else:
            return
        if url:
            self.urls.add(url)


def check_page_assets(base, html):
    """Fresh unauthenticated HTTP requests: HTML 200 is not proof of a usable UI."""
    parser = PageAssets()
    parser.feed(html)
    assert parser.urls, "Page has no UI assets"
    for url in sorted(parser.urls):
        assert url.startswith("/static/"), f"UI asset still depends on another server: {url}"
        extension = Path(parse.urlsplit(url).path).suffix.lower()
        expected = {".css": "text/css", ".js": "javascript", ".png": "image/png",
            ".svg": "image/svg+xml", ".ico": "image/"}.get(extension)
        assert expected, f"Unchecked asset type: {url}"
        req = request.Request(base + url, headers={"Cache-Control": "no-cache"})
        with request.urlopen(req, timeout=5) as response:
            body = response.read()
            assert response.status == 200 and expected in response.headers.get("Content-Type", ""), (
                url, response.status, response.headers.get("Content-Type"), body[:100])
            assert body and not body.lstrip().lower().startswith((b"<!doctype html", b"<html", b"<main")), url
            assert not response.headers.get("X-Nova-Mode"), "Static request entered database routing"
    return len(parser.urls)


def seed(reference_only=False, journal_preview=False):
    from local_pos.integrated_config import load_config, local_database
    from local_pos.integrated_configuration import configure
    profile = importlib.import_module("NovaSoft.integrated_process_test_settings")
    config = load_config(os.environ["NOVA_INTEGRATED_CONFIG"], ROOT)
    if reference_only:
        from local_pos.reference_runtime import configure_reference
        profile.LOCAL_CONFIG = config
        profile.DATABASES["offline"] = local_database(config)
        configure_reference(vars(profile))
    else:
        configure(vars(profile), config, local_database(config), journal=journal_preview)
    os.environ["DJANGO_SETTINGS_MODULE"] = profile.__name__
    import django
    django.setup()
    from mainApp.models import Usuario, Rol, Sucursal, Empleado, Categoria, Producto, PuntosPago, Inventario
    role = Rol.objects.create(nombre="Web Master")
    user = Usuario.objects.create_user("integrated-test", "test-only", rolid=role)
    branch = Sucursal.objects.create(nombre="Cloud branch")
    Empleado.objects.create(usuarioid=user, sucursalid=branch, nombre="FICTIONAL", apellido="ONLY",
                            numerodocumento="NO-REAL-DATA")
    category = Categoria.objects.create(nombre="FICTIONAL CATEGORY")
    product = Producto.objects.create(nombre="FICTIONAL LOCAL PRODUCT", precio="2000", categoria=category)
    point = PuntosPago.objects.create(nombre="FICTIONAL POINT", sucursalid=branch)
    Inventario.objects.create(productoid=product, sucursalid=branch, cantidad=-7)
    if journal_preview:
        from mainApp.models import ConfiguracionFuncionalidad, MetodoPago
        from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE
        ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=False)
        MetodoPago.objects.get_or_create(codigo="efectivo", defaults={"nombre": "Efectivo", "activo": True, "es_efectivo": True})
    if not reference_only:
        # Download through the real service instead of fabricating a valid
        # local snapshot. Distinct names after capture identify the read source.
        from django.test import override_settings
        from local_pos.integrated_replica import synchronize
        with override_settings(NOVA_REFERENCE_ENABLED=True):
            synchronize(user.pk, point.pk)
    Producto.objects.filter(pk=product.pk).update(nombre="FICTIONAL CLOUD PRODUCT")
    print(json.dumps({"user_id": user.pk, "point_id": point.pk, "branch_id": branch.pk, "product_id": product.pk}), flush=True)


def child_server(port, autoreload=False, reference_only=False, journal_preview=False):
    # This alias exists solely in this disposable test interpreter.
    profile = importlib.import_module("NovaSoft.integrated_process_test_settings")
    sys.modules["NovaSoft.settings"] = profile
    os.environ.pop("DJANGO_SETTINGS_MODULE", None)
    sys.argv = [str(ROOT / "manage.py"), "runserver", f"127.0.0.1:{port}"]
    if autoreload:
        from django.utils import autoreload as reloader
        # Reloader children MUST reinject the fictional profile too. Never let
        # a test reloader fall back to the real Aiven settings in manage.py.
        reloader.get_child_arguments = lambda: [sys.executable, "-B", __file__, "--child-server", str(port), "--autoreload", *(["--reference-only"] if reference_only else []), *(["--journal-preview"] if journal_preview else [])]
    else:
        sys.argv.append("--noreload")
    import manage
    if os.name == "nt":
        def interrupt(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK, interrupt)
    manage.main()


def stop_server(process):
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=10)


def check_reference_case(client, base, fixture, cloud_config, local_config):
    """Real HTTP/manual preparation/background updates; ONLY fictional DBs."""
    import psycopg2
    def sql(config, statement):
        connection = psycopg2.connect(host="127.0.0.1", port=config["port"],
            dbname=config["database"], user=config["user"], password=config["password"], connect_timeout=3)
        try:
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute(statement)
                    return cursor.fetchone() if cursor.description else None
        finally:
            connection.close()
    def get(path):
        with client.open(base + path, timeout=10) as response:
            return json.load(response)
    def wait_until(predicate, message):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.5)
        raise AssertionError(message)
    data = parse.urlencode({"nombreusuario": "integrated-test", "contraseña": "test-only",
        "next": "/test/identity/"}).encode()
    with client.open(base + "/", data=data, timeout=10) as response:
        assert json.load(response)["user"] == fixture["user_id"]
    assert sql(cloud_config, "SELECT count(*) FROM django_session")[0] == 1
    assert sql(local_config, "SELECT count(*) FROM django_session")[0] == 0, "Reference mode changed session storage"
    assert not get("/local/copia/?format=json")["has_copy"]
    with client.open(base + "/local/copia/", data=parse.urlencode({"point_id": fixture["point_id"]}).encode(), timeout=10) as response:
        html = response.read().decode()
        assert 'id="reference-copy"' in html and "reference_copy.js" in html
    wait_until(lambda: get("/local/copia/?format=json")["ready"], "Initial background snapshot did not finish")
    assert sql(local_config, "SELECT cantidad FROM inventario")[0] == -7
    assert sql(local_config, "SELECT precio FROM productos")[0] == 2000
    # A fictional source change followed by normal authenticated activity must
    # refresh the existing copy without the user pressing its button again.
    sql(cloud_config, "UPDATE productos SET precio=2300")
    with client.open(base + "/test/identity/", data=b"fixture=1", timeout=10) as response:
        assert json.load(response)["user"] == fixture["user_id"]
    wait_until(lambda: sql(local_config, "SELECT precio FROM productos")[0] == 2300,
        "Automatic refresh did not copy the changed cloud price")
    assert sql(local_config, "SELECT count(*) FROM django_session")[0] == 0
    assert get("/autocomplete/producto-global/?term=FICTIONAL")["results"][0]["text"] == "FICTIONAL CLOUD PRODUCT"
    assert sql(cloud_config, "SELECT cantidad FROM inventario")[0] == -7
    print("PASS: original Windows runserver, cloud sessions/login/autocomplete unchanged, real preparation page, background catalog refresh and negative inventory.", flush=True)


def run(args):
    from local_pos.integrated_prepare import prepare
    from local_pos.runtime import Postgres, port_free, private_dir
    from local_pos.wsl_postgres import WslPostgres
    for port in (args.cloud_port, args.local_port, args.http_port):
        port_free(port)
    if len({args.cloud_port, args.local_port, args.http_port}) != 3:
        raise RuntimeError("The test needs three distinct free ports.")
    # WSL may discard /tmp when its VM stops. Keep diagnostic evidence under
    # the test user's private home instead; never reuse an existing cluster.
    test_root = private_dir(Path.home() / ".novapos-tests")
    root = Path(tempfile.mkdtemp(prefix="integrated-", dir=test_root))
    if os.name == "posix":
        root.chmod(0o700)
    print(f"Fictional databases and logs: {root}", flush=True)
    cloud_path, local_path = root / "cloud/config.json", root / "local/config.json"
    backend = {"wsl_distribution": args.wsl_distribution, "wsl_user": args.wsl_user,
        "integrated_preview": not args.reference_only, "journal_preview": args.journal_preview}
    cloud_config = prepare(ROOT, cloud_path, args.pg_bin, port=args.cloud_port, **backend)
    local_config = prepare(ROOT, local_path, args.pg_bin, port=args.local_port, **backend)
    engine = WslPostgres if args.wsl_distribution else Postgres
    cloud = engine(cloud_path.parent, args.pg_bin, cloud_config)
    local = engine(local_path.parent, args.pg_bin, local_config)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith("PG") and key not in {"DJANGO_SETTINGS_MODULE", "RUN_MAIN", "DATABASE_URL", "NOVA_LOCAL_CONFIG"}}
    environment.update(HYBRID_TEST_POSTGRES="yes-local-disposable", NOVA_TEST_CLOUD_CONFIG=str(cloud_path),
                       NOVA_INTEGRATED_CONFIG=str(local_path), PYTHONIOENCODING="utf-8")
    server_command = [sys.executable, "-B", __file__, "--child-server", str(args.http_port)]
    if args.autoreload:
        server_command.append("--autoreload")
    if args.reference_only:
        server_command.append("--reference-only")
    if args.journal_preview:
        server_command.append("--journal-preview")
    process = None
    cloud_started = local_started = False
    log = None
    try:
        cloud.start()
        cloud_started = True
        local.start()
        local_started = True
        # SQL from this interpreter (Windows when testing WSL), including actual
        # PL/pgSQL execution and rollback. Only our new fictional database.
        import psycopg2
        connection = psycopg2.connect(host="127.0.0.1", port=args.local_port,
            dbname=local_config["database"], user=local_config["user"], password=local_config["password"])
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT version()")
                print("Database engine: " + cursor.fetchone()[0], flush=True)
                cursor.execute("CREATE TEMP TABLE nova_probe (value integer)")
                cursor.execute("DO $$ BEGIN INSERT INTO nova_probe VALUES (42); END $$ LANGUAGE plpgsql")
                cursor.execute("UPDATE nova_probe SET value=value+1 RETURNING value")
                assert cursor.fetchone()[0] == 43
                cursor.execute("DELETE FROM nova_probe RETURNING value")
                assert cursor.fetchone()[0] == 43
            connection.rollback()
        finally:
            connection.close()
        print("PASS: client SQL INSERT/SELECT/UPDATE/DELETE + PL/pgSQL, transaction rolled back.", flush=True)
        seed_command = [sys.executable, "-B", __file__, "--child-seed", *(["--reference-only"] if args.reference_only else []), *(["--journal-preview"] if args.journal_preview else [])]
        seeded = subprocess.run(seed_command, env=environment, cwd=ROOT, check=True, timeout=60, capture_output=True, text=True)
        fixture = json.loads(seeded.stdout.strip().splitlines()[-1])
        local.stop()
        local_started = False
        log = (root / "runserver.log").open("w", encoding="utf-8")
        process = subprocess.Popen(server_command,
            env=environment, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        jar = CookieJar()
        client = request.build_opener(request.HTTPCookieProcessor(jar))
        base = f"http://127.0.0.1:{args.http_port}"

        def get_response(mode, path="/test/identity/"):
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"runserver stopped unexpectedly; inspect {root / 'runserver.log'}")
                try:
                    req = request.Request(base + path, headers={"Accept": "application/json"})
                    with client.open(req, timeout=3) as response:
                        data = json.load(response)
                        if response.headers.get("X-Nova-Mode") == mode:
                            return data
                except (error.URLError, TimeoutError, OSError):
                    pass
                time.sleep(0.3)
            raise RuntimeError(f"Timed out waiting for {mode}; inspect {root / 'runserver.log'}")

        get_response(None if args.reference_only else "online")
        if args.reference_only:
            check_reference_case(client, base, fixture, cloud_config, local_config)
            return  # finally still stops ONLY the two newly created engines.
        # Authenticate through the ORIGINAL application login, redirecting to
        # the read-only test probe instead of rendering a business dashboard.
        data = parse.urlencode({"nombreusuario": "integrated-test", "contraseña": "test-only",
                                "next": "/test/identity/"}).encode()
        with client.open(base + "/", data=data, timeout=5) as response:
            user = json.load(response)["user"]
        assert user is not None, "The original login did not authenticate."
        if args.journal_preview:
            from uuid import uuid4
            operation_id = str(uuid4())
            body = parse.urlencode({"sucursal": fixture["branch_id"], "puntopago": fixture["point_id"],
                "productos": json.dumps([fixture["product_id"]]), "cantidades": "[2]", "medio_pago": "efectivo",
                "pagos": '[{"medio_pago":"efectivo","monto":"4000"}]', "efectivo_recibido": "5000"}).encode()
            def sell():
                req = request.Request(base + "/generar_venta/", data=body, headers={"X-Nova-Operation-ID": operation_id,
                    "X-Nova-Expected-Total": "4000"})
                with client.open(req, timeout=10) as response:
                    return json.load(response)
            first = sell()
            assert first.get("success"), first
            assert sell()["venta_id"] == first["venta_id"]
            cloud.stop()
            cloud_started = False
            # Simulate a crash AFTER cloud commit but BEFORE the local receipt
            # was saved. Only the brand-new fixture database is modified here.
            connection = psycopg2.connect(host="127.0.0.1", port=args.local_port,
                dbname=local_config["database"], user=local_config["user"], password=local_config["password"])
            with connection:
                with connection.cursor() as cursor:
                    cursor.execute("UPDATE local_pos_localcommand SET state='intent', local_result=%s::jsonb WHERE operation_id=%s",
                        [json.dumps({"outcome": "sending", "attempt": 1}), operation_id])
            connection.close()
            stop_server(process)
            assert not local.status(), "journal test left its private engine running"
            process = subprocess.Popen(server_command, env=environment, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            confirmation_path = f"/local/ventas/confirmacion/{operation_id}/"
            assert get_response(None, confirmation_path)["status"] == "pending"
            online_operation = operation_id
            get_response("offline", "/autocomplete/producto-global/?term=FICTIONAL")
            with client.open(base + "/generar_venta/", timeout=5) as response:
                assert response.status == 200 and "Generar Venta" in response.read().decode()
            # A fresh HTTP client has no page cache, just the shared browser
            # session: equivalent to opening another tab during the outage.
            new_tab = request.build_opener(request.HTTPCookieProcessor(jar))
            for path, marker in (("/home/", "Panel de control"),
                                 ("/generar_venta/", "Generar Venta"),
                                 ("/visor/cajeros/", "Encuentra tu producto"),
                                 ("/agregar_producto/", "Estamos offline")):
                with new_tab.open(base + path, timeout=5) as response:
                    html = response.read().decode()
                    assert response.status == 200 and marker in html, path
                    assert 'class="nv"' in html and "integrated_navigation.js" in html, path
                check_page_assets(base, html)
            with new_tab.open(base + "/local/disponibilidad/?path=/agregar_producto/", timeout=5) as response:
                assert json.load(response) == {"allowed": False, "offline": True,
                    "message": "Estamos offline. Esta opción necesita conexión a internet. Puedes seguir trabajando en las funciones disponibles."}
            print("PASS: fresh offline tab renders home/sale/scanner and unavailable-page UI; every linked CSS/JS/image returns real static content without session, cloud routing or browser cache.", flush=True)
            if args.preview_seconds:
                print(f"Offline UI preview for {args.preview_seconds}s: {base}/generar_venta/ (fictional user integrated-test)", flush=True)
                time.sleep(args.preview_seconds)
            operation_id = str(uuid4())
            offline_one = sell()
            assert offline_one.get("offline_saved"), offline_one
            assert sell()["venta_id"] == offline_one["venta_id"]
            offline_one_id = operation_id
            # NEW sale survives a process restart while the cloud is stopped.
            stop_server(process)
            assert not local.status()
            process = subprocess.Popen(server_command, env=environment, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
            stored = get_response(None, f"/local/ventas/confirmacion/{offline_one_id}/")
            assert stored["status"] == "saved_offline"
            assert stored["response"]["venta_id"] == offline_one["venta_id"]
            assert get_response(None, "/local/ventas/estado/")["pending"] == 1
            get_response("offline", "/autocomplete/producto-global/?term=FICTIONAL")
            operation_id = str(uuid4())
            offline_two = sell()
            assert offline_two.get("offline_saved"), offline_two
            offline_two_id = operation_id
            cloud.start()
            cloud_started = True
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                recovered = get_response(None, confirmation_path)
                if recovered["status"] == "confirmed":
                    break
                time.sleep(.5)
            else:
                raise AssertionError("Background worker did not recover the original cloud confirmation")
            assert recovered["response"]["venta_id"] == first["venta_id"]
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                statuses = [get_response(None, f"/local/ventas/confirmacion/{key}/")
                    for key in (offline_one_id, offline_two_id)]
                if all(row.get("synchronization") == "accepted" for row in statuses):
                    break
                time.sleep(.5)
            else:
                raise AssertionError("New offline sales were not uploaded automatically")
            assert len({row["cloud_sale_id"] for row in statuses}) == 2
            summary = get_response(None, "/local/ventas/estado/")
            assert summary["pending"] == 0 and summary["review"] == 0
            get_response("online")  # Only after acknowledgement AND fresh catalog.
            operation_id = offline_one_id
            assert sell()["venta_id"] == offline_one["venta_id"], "Delayed retry duplicated an offline sale"
            connection = psycopg2.connect(host="127.0.0.1", port=args.cloud_port,
                dbname=cloud_config["database"], user=cloud_config["user"], password=cloud_config["password"])
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM ventas")
                assert cursor.fetchone()[0] == 3
                cursor.execute("SELECT cantidad FROM inventario")
                assert cursor.fetchone()[0] == -13
            connection.close()
            print("PASS: original sale + two NEW offline sales, real outage, restarts, stable UUID retries, automatic upload/refresh, three cloud sales and exact negative stock.", flush=True)
            return
        online = get_response("online")
        assert online["user"] == user and online["branches"] == ["Cloud branch"], online
        lookup = "/autocomplete/producto-global/?term=FICTIONAL"
        assert get_response("online", lookup)["results"][0]["text"] == "FICTIONAL CLOUD PRODUCT"
        cookies = {(cookie.name, cookie.value) for cookie in jar}
        cloud.stop()  # A real unavailable database, not a mocked health flag.
        cloud_started = False
        offline = get_response("offline", lookup)
        assert offline["results"][0]["text"] == "FICTIONAL LOCAL PRODUCT", offline
        assert cookies == {(cookie.name, cookie.value) for cookie in jar}, "The session changed during the outage."
        try:
            client.open(base + "/test/identity/", data=b"forbidden=1", timeout=5)
            raise AssertionError("An offline write was accepted without a journal adapter.")
        except error.HTTPError as exc:
            assert exc.code == 503, exc.code
        cloud.start()
        cloud_started = True
        restored = get_response("online")
        assert restored["user"] == user and restored["branches"] == ["Cloud branch"], restored
        print("PASS: original login and product autocomplete, same process + cookie, cloud -> real outage -> local -> cloud; unjournaled writes blocked.", flush=True)
        # Restart the application while the fictional cloud is STILL offline.
        # This checks durable sessions and startup, not just a warm connection.
        cloud.stop()
        cloud_started = False
        stop_server(process)
        assert not local.status(), "runserver left its private cluster running."
        process = subprocess.Popen(server_command,
            env=environment, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
        assert get_response("offline", lookup)["results"][0]["text"] == "FICTIONAL LOCAL PRODUCT"
        assert cookies == {(cookie.name, cookie.value) for cookie in jar}
        cloud.start()
        cloud_started = True
        assert get_response("online")["user"] == user
        print("PASS: restart while offline preserves the original session and reads; reconnect resumes cloud reads.", flush=True)
    except BaseException:
        if log:
            log.flush()
            print("Runserver diagnostics (fictional data only):", flush=True)
            print((root / "runserver.log").read_text(encoding="utf-8", errors="replace")[-12000:], flush=True)
        raise
    finally:
        stop_server(process)
        if log:
            log.close()
        # All clusters below were freshly created by this test, never system services.
        unexpectedly_running = local.status()
        if unexpectedly_running:
            local.stop()
        if cloud_started:
            cloud.stop()
        if unexpectedly_running and not local_started:
            raise RuntimeError("runserver failed to stop its private cluster; the test stopped it safely.")
        if args.reference_only or args.journal_preview:
            print("Test cleanup: owned app/cloud stopped; existing databases untouched.", flush=True)
    print("PASS: runserver released its private PostgreSQL; fictional cloud also stopped.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin")
    parser.add_argument("--wsl-distribution")
    parser.add_argument("--wsl-user")
    parser.add_argument("--autoreload", action="store_true", help="Also exercise Django's parent/child reloader.")
    parser.add_argument("--reference-only", action="store_true", help="Test the WSL reference-copy profile, without enabling experimental offline writes.")
    parser.add_argument("--journal-preview", action="store_true", help="Test original online/offline sales, restart, upload and lost-acknowledgement recovery.")
    parser.add_argument("--cloud-port", type=int, default=55483)
    parser.add_argument("--local-port", type=int, default=55484)
    parser.add_argument("--http-port", type=int, default=8935)
    parser.add_argument("--preview-seconds", type=int, choices=range(0, 121), default=0,
                        help="Keep the fictional cloud offline briefly for a manual browser check (0-120 seconds).")
    parser.add_argument("--child-seed", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--child-server", type=int, help=argparse.SUPPRESS)
    options = parser.parse_args()
    if options.child_seed:
        seed(options.reference_only, options.journal_preview)
    elif options.child_server:
        child_server(options.child_server, options.autoreload, options.reference_only, options.journal_preview)
    elif not options.pg_bin:
        parser.error("--pg-bin is required")
    elif options.reference_only and not options.wsl_distribution:
        parser.error("--reference-only currently requires the Windows/WSL development profile")
    elif options.reference_only and options.journal_preview:
        parser.error("Choose either --reference-only or --journal-preview")
    else:
        run(options)
