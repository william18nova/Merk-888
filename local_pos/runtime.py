"""Persistent per-user PostgreSQL + Django runtime, never cloud settings.

No database is initialized, migrated, reset or seeded by `serve`. Every lifecycle
operation takes an exclusive OS lock. Production enrollment is an explicit CLI
step; initialization creates an empty, unbound installation, not a cloud clone.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler
from uuid import UUID, uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "nova-full-local-installation-v1"
PACKAGE_FORMAT = "nova-full-local-package-v1"
DB = "nova_full_local_lab"  # Deliberately cannot point at a business database.
ADMIN = "nova_local_owner"


def _stream_hash(stream):
    digest = hashlib.sha256()
    while block := stream.read(1024 * 1024):
        digest.update(block)
    return digest.hexdigest()


def _file_hash(path):
    with path.open("rb") as stream:
        return _stream_hash(stream)


def private_dir(path):
    path = Path(path).absolute()
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise RuntimeError("No se permiten enlaces simbólicos en la instalación.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        if path.stat().st_uid != os.getuid():
            raise RuntimeError("El directorio no pertenece al usuario actual.")
        path.chmod(0o700)
    else:
        # Strip inherited access. Do not rely on Python chmod for Windows ACLs.
        sid = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                             check=True, capture_output=True, text=True).stdout
        import csv
        sid = next(csv.reader([sid.strip()]))[1]
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


def save_json(path, value):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("No se permite escribir a través de un enlace.")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".new")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise RuntimeError("Archivo privado inexistente o inválido.")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise RuntimeError("El archivo privado permite acceso a otros usuarios.")
    return json.loads(path.read_text(encoding="utf-8"))


@contextmanager
def installation_lock(root):
    root = Path(root)
    lock = root / "runtime.lock"
    if lock.is_symlink():
        raise RuntimeError("El bloqueo de la instalación no es válido.")
    with lock.open("a+b") as stream:
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                stream.write(b"0")
                stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("La instalación está en uso. Ciérrala antes de continuar.") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def package_manifest(root=ROOT):
    root = Path(root)
    path = root / "full-local-package.json"
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("Usa la distribución verificada creada por build_full_local.py, no el repositorio cambiante.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != PACKAGE_FORMAT or not isinstance(data.get("files"), dict):
        raise RuntimeError("Manifiesto de distribución inválido.")
    if hashlib.sha256(json.dumps(data["files"], sort_keys=True).encode()).hexdigest() != data.get("package_id"):
        raise RuntimeError("La identidad del paquete no coincide con su manifiesto.")
    for name, expected in data["files"].items():
        rel = Path(name)
        candidate = root / rel
        if (rel.anchor or ".." in rel.parts or "\\" in name or ":" in name
                or any(part.is_symlink() for part in (candidate, *candidate.parents))
                or not candidate.is_file()):
            raise RuntimeError("Ruta inválida en la distribución.")
        if hashlib.sha256(candidate.read_bytes()).hexdigest() != expected:
            raise RuntimeError("La distribución fue modificada. No se iniciará con archivos mezclados.")
    return data


def installed_state(root=None):
    config = Path(os.environ.get("NOVA_LOCAL_CONFIG", ""))
    root = Path(root) if root is not None else config.parent
    if not root.is_absolute():
        raise RuntimeError("Se necesita una ruta privada absoluta.")
    if (root / "upgrade-intent.json").exists():
        raise RuntimeError("Hay una actualización incompleta. Reanuda su recuperación antes de abrir el POS.")
    state = read_json(root / "installation.json")
    local = read_json(root / "local.json")
    if (state.get("format") != FORMAT or state.get("phase") != "ready"
            or state.get("instance_id") != local.get("instance_id")
            or Path(local.get("data_dir", "")).resolve() != root.resolve()
            or state.get("system") != platform.system()):
        raise RuntimeError("Instalación incompleta o identidad/ubicación diferente; no se cambiarán datos.")
    UUID(state["instance_id"])
    package = package_manifest()
    if state.get("package_id") != package.get("package_id"):
        raise RuntimeError("La versión instalada no coincide. Se requiere actualización explícita con respaldo.")
    if not (root / "postgres/PG_VERSION").is_file():
        raise RuntimeError("Falta el clúster persistente. No se creará una base vacía encima.")
    return state


def port_free(port):
    if type(port) is not int or not 1024 <= port <= 65535:
        raise RuntimeError("Puerto local inválido.")
    with socket.socket() as sock:
        if os.name == "posix":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            raise RuntimeError(f"El puerto local {port} está ocupado; no se usará otro servidor.") from None


class Postgres:
    def __init__(self, root, pg_bin, config):
        self.root, self.bin, self.config = Path(root), Path(pg_bin), config
        if not self.bin.is_absolute():
            raise RuntimeError("Indica la carpeta absoluta de PostgreSQL.")
        for command in ("initdb", "pg_ctl", "createdb", "pg_dump", "pg_restore"):
            if not self.executable(command).is_file():
                raise RuntimeError("PostgreSQL local no está instalado o falta una herramienta.")

    def executable(self, name):
        return self.bin / (name + (".exe" if os.name == "nt" else ""))

    def run(self, name, *args, check=True, capture=False):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("PG") and key not in {"DATABASE_URL", "DJANGO_SETTINGS_MODULE"}}
        # Credentials go to the child environment, never argv/log output.
        env["PGPASSWORD"] = self.config["password"]
        return subprocess.run([str(self.executable(name)), *map(str, args)], env=env,
                              stdin=subprocess.DEVNULL, check=check,
                              capture_output=capture, text=capture)

    def status(self):
        return self.run("pg_ctl", "-D", self.root / "postgres", "status", check=False, capture=True).returncode == 0

    def start(self):
        if self.status():
            raise RuntimeError("Este clúster ya está activo; no se iniciará otra instancia.")
        port_free(self.config["port"])
        version = self.run("pg_ctl", "--version", capture=True).stdout.split()[2].split(".")[0]
        if (self.root / "postgres/PG_VERSION").read_text().strip() != version:
            raise RuntimeError("PostgreSQL tiene otra versión mayor. Hace falta una actualización supervisada.")
        socket_option = f' -k "{self.root}"' if os.name == "posix" else ""
        self.run("pg_ctl", "-D", self.root / "postgres", "-l", self.root / "postgres.log", "-o",
                 f'-h 127.0.0.1 -p {self.config["port"]}{socket_option} -c max_connections=40', "-w", "-t", "30", "start")

    def stop(self):
        self.run("pg_ctl", "-D", self.root / "postgres", "-m", "fast", "-w", "-t", "30", "stop")


def django_setup(root, *, initializing=False):
    os.environ["NOVA_LOCAL_CONFIG"] = str(Path(root) / "local.json")
    os.environ["DJANGO_SETTINGS_MODULE"] = (
        "NovaSoft.hybrid_local_settings" if initializing else "NovaSoft.hybrid_installed_settings")
    import django
    django.setup()
    if "NovaSoft.settings" in sys.modules:
        raise RuntimeError("Se cargó una configuración de nube inesperada.")


def initialize(root, pg_bin, *, http_port=8910, pg_port=55441, username, password):
    root = Path(root).absolute()
    if root == ROOT or ROOT in root.parents:
        raise RuntimeError("Guarda la instalación y sus credenciales fuera del repositorio/distribución.")
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("El destino ya contiene datos. Usa serve para reanudar, no init.")
    if not isinstance(password, str) or len(password) < 12 or not username.strip():
        raise RuntimeError("Usa un usuario y una contraseña local de al menos 12 caracteres.")
    if http_port == pg_port:
        raise RuntimeError("El puerto web y PostgreSQL deben ser distintos.")
    port_free(http_port)
    port_free(pg_port)
    package = package_manifest()
    root = private_dir(root)
    config = {"application": "nova-full-local-development-v1", "mode": "development_readonly",
              "instance_id": str(uuid4()), "secret_key": secrets.token_hex(48), "database": DB,
              "user": DB, "password": secrets.token_hex(32), "port": pg_port, "data_dir": str(root)}
    pg = Postgres(root, pg_bin, config)
    state = {"format": FORMAT, "phase": "initializing", "instance_id": config["instance_id"],
             "package_id": package["package_id"], "system": platform.system(),
             "pg_bin": str(Path(pg_bin).absolute()), "http_port": http_port,
             "created_at": datetime.now(timezone.utc).isoformat()}
    save_json(root / "local.json", config)
    save_json(root / "installation.json", state)
    with installation_lock(root):
        password_file = root / "bootstrap-password"
        with password_file.open("x", encoding="utf-8") as stream:
            stream.write(secrets.token_hex(48))
        if os.name == "posix":
            password_file.chmod(0o600)
        try:
            pg.run("initdb", "-D", root / "postgres", "-U", ADMIN, "-A", "scram-sha-256",
                   "-E", "UTF8", "--locale=C", f"--pwfile={password_file}")
            pg.start()
            try:
                import psycopg2
                from psycopg2 import sql
                admin_password = password_file.read_text()
                connection = psycopg2.connect(host="127.0.0.1", port=pg_port, dbname="postgres", user=ADMIN,
                                             password=admin_password, connect_timeout=5)
                try:
                    connection.autocommit = True
                    with connection.cursor() as cursor:
                        cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %s").format(sql.Identifier(DB)), [config["password"]])
                        cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(DB), sql.Identifier(DB)))
                finally:
                    connection.close()
                django_setup(root, initializing=True)
                from django.core.management import call_command
                from local_pos.models import LocalNode
                from mainApp.models import Rol, Usuario
                from mainApp.permissions import grant_all_permissions_to_web_master
                call_command("check")
                # One-time empty-schema installation only; never run on serve.
                call_command("migrate", run_syncdb=True, interactive=False, verbosity=0)
                role = Rol.objects.create(nombre="Web Master")
                user = Usuario.objects.create_user(username.strip(), password, rolid=role)
                grant_all_permissions_to_web_master(role.pk)
                LocalNode.objects.create(id=config["instance_id"])
                call_command("collectstatic", interactive=False, verbosity=0)
                shutil.copytree(ROOT / "offline_vendor", root / "staticfiles/local_vendor", dirs_exist_ok=True)
                state.update(phase="ready", local_user_id=user.pk)
                save_json(root / "installation.json", state)
            finally:
                from django.db import connections
                connections.close_all()
                pg.stop()
        finally:
            password_file.unlink(missing_ok=True)
    print("Instalación privada creada. No está vinculada y no se cargaron datos reales.")


def serve(root, *, ready_callback=None):
    state = installed_state(root)
    if (Path(root) / "pilot-hub.json").exists():
        raise RuntimeError("Esta carpeta es el servidor ficticio, no una caja. Usa pilot hub-serve.")
    if any((Path(root) / name).exists() for name in ("handoff-intent.json", "next-session-intent.json")):
        raise RuntimeError("Hay un cambio de cajero/turno incompleto. Repite el mismo comando de relevo; no se iniciará una sesión mezclada.")
    config = read_json(Path(root) / "local.json")
    pg = Postgres(root, state["pg_bin"], config)
    with installation_lock(root):
        port_free(state["http_port"])
        pg.start()
        stop = threading.Event()
        worker = None
        try:
            django_setup(root)
            from django.conf import settings
            from django.core.wsgi import get_wsgi_application
            from django.db import connections
            from whitenoise import WhiteNoise
            from waitress import create_server
            manifest = Path(root) / "staticfiles/local_vendor/manifest.json"
            settings.LOCAL_ASSET_MAP = json.loads(manifest.read_text())["urls"]
            binding = Path(root) / "replica-connection.json"
            if binding.exists():
                from local_pos.replica_transport import ReplicaRemote
                from local_pos.sales import sync_cycle
                remote = ReplicaRemote(read_json(binding))  # HTTPS required in installed mode.
                def synchronize():
                    delay = 1
                    while not stop.wait(delay):
                        try:
                            sync_cycle(remote, local_user_id=state["local_user_id"])
                            delay = 30
                        except Exception:
                            delay = min(max(delay * 2, 30), 300)
                        finally:
                            connections.close_all()
                worker = threading.Thread(target=synchronize, daemon=True)
                worker.start()
            app = WhiteNoise(get_wsgi_application(), root=str(Path(root) / "staticfiles"), prefix="static/")
            server = create_server(app, host="127.0.0.1", port=state["http_port"], threads=4,
                                   channel_timeout=60, max_request_body_size=2_000_000, clear_untrusted_proxy_headers=True)
            if ready_callback:
                ready_callback(server)
            print(f"POS local: http://127.0.0.1:{state['http_port']}/local/estado/", flush=True)
            try:
                server.run()
            finally:
                server.close()
        finally:
            stop.set()
            if worker:
                worker.join(timeout=35)
            from django.db import connections
            connections.close_all()
            pg.stop()


def pairing_call(url, action, payload, credential=None):
    from hybrid_client.client import NoRedirects, cloud_url
    url = cloud_url(url)  # Deliberately no HTTP or loopback override for enrollment.
    from .pilot_tls import context_for
    tls_context = context_for(url)
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential['device_id']}.{credential['secret']}"
    request = Request(url + "/api/hybrid/v1/" + action + "/", method="POST",
                      data=json.dumps(payload).encode(), headers=headers)
    try:
        with build_opener(NoRedirects(), HTTPSHandler(context=tls_context)).open(request, timeout=15) as response:
            data = json.loads(response.read(100_001))
        if not isinstance(data, dict) or data.get("ok") is not True:
            raise ValueError
        return data
    except (HTTPError, URLError, OSError, ValueError):
        raise RuntimeError("El servidor no confirmó la vinculación/sesión. Los datos locales se conservaron; revisa autorización y conexión.") from None


def pair(root, url, *, code, username, password):
    from hybrid_client.client import cloud_url
    from local_pos.replica_transport import ReplicaRemote
    state = installed_state(root)
    root, url = Path(root), cloud_url(url)
    with installation_lock(root):
        if (root / "replica-connection.json").exists():
            raise RuntimeError("Ya hay una vinculación. No se reemplaza identidad ni sesión con este comando.")
        pending = root / "pairing-intent.json"
        intent = read_json(pending) if pending.exists() else {
            "url": url, "secret": secrets.token_hex(32), "session_id": str(uuid4()), "username": username}
        if intent["url"] != url or intent["username"] != username:
            raise RuntimeError("Hay otra vinculación incompleta. Reintenta con el mismo servidor y usuario.")
        save_json(pending, intent)  # Stable credential + session before any network request.
        if not intent.get("device_id"):
            enrolled = pairing_call(url, "enroll", {"code": code, "secret": intent["secret"]})
            intent["device_id"] = str(UUID(enrolled["device_id"]))
            save_json(pending, intent)
        session = pairing_call(url, "session", {"username": username, "password": password,
                              "session_id": intent["session_id"]}, credential=intent)
        if session.get("session_id") != intent["session_id"] or session.get("user") != username:
            raise RuntimeError("La sesión no coincide con el usuario/identidad solicitados.")
        binding = {key: intent[key] for key in ("url", "device_id", "secret", "session_id")}
        ReplicaRemote(binding)  # Validate everything before storing final enrollment.
        save_json(root / "replica-connection.json", binding)
        pending.unlink()
    print("Vinculación guardada. Inicia el POS para descargar la referencia autorizada; no se guardó la contraseña de la nube.")


def next_session(root, *, username, password):
    """Solo después de un cierre confirmado; nunca abandona una cola pendiente."""
    from local_pos.replica_transport import ReplicaRemote
    state = installed_state(root)
    root = Path(root)
    with installation_lock(root):
        if (root / "handoff-intent.json").exists():
            raise RuntimeError("Hay un relevo de cajero pendiente. Reanuda switch-user con el mismo usuario.")
        pg = Postgres(root, state["pg_bin"], read_json(root / "local.json"))
        pg.start()
        try:
            django_setup(root)
            from local_pos.models import LocalSaleSession, LocalCommand
            from local_pos.sales import sync_cycle
            session = LocalSaleSession.objects.get(node_id=state["instance_id"])
            if LocalCommand.objects.exclude(state="accepted").exists():
                raise RuntimeError("Hay operaciones pendientes o en revisión. Sincroniza antes de cambiar de turno.")
            intent_path = root / "next-session-intent.json"
            if intent_path.exists():
                intent = read_json(intent_path)
            else:
                if not session.data.get("closed") or session.data.get("user") != username:
                    raise RuntimeError("Primero confirma el cierre. Esta versión solo renueva sesiones del mismo usuario vinculado.")
                intent = {"username": username, "previous": str(session.session_id), "requested": str(uuid4())}
                save_json(intent_path, intent)
            if intent["username"] != username:
                raise RuntimeError("Reintenta la renovación con el mismo usuario solicitado.")
            binding = read_json(root / "replica-connection.json")
            data = pairing_call(binding["url"], "session", {"username": username, "password": password,
                                "session_id": intent["requested"]}, credential=binding)
            from local_pos.sessions import adopt_session
            # Identidad estable en disco antes de activar datos nuevos. Si se
            # interrumpe, serve falla cerrado y next-session reanuda el intento.
            new_binding = {**binding, "session_id": intent["requested"]}
            ReplicaRemote(new_binding)
            save_json(root / "replica-connection.json", new_binding)
            adopt_session(data, previous_id=intent["previous"], requested_id=intent["requested"])
            sync_cycle(ReplicaRemote(new_binding), local_user_id=state["local_user_id"])
            intent_path.unlink()
        finally:
            from django.db import connections
            connections.close_all()
            pg.stop()
    print("Nuevo turno autorizado. Historial e identidad anteriores conservados.")


def switch_user(root, *, username, password, local_password):
    """Relevo online con intención durable y cuentas locales distintas."""
    from local_pos.replica_transport import ReplicaRemote
    if not username or len(local_password) < 12 or local_password == password:
        raise RuntimeError("Elige una contraseña LOCAL de al menos 12 caracteres y diferente de la contraseña de la nube.")
    state = installed_state(root)
    root = Path(root)
    with installation_lock(root):
        if (root / "next-session-intent.json").exists():
            raise RuntimeError("Hay una renovación de turno pendiente. Reanuda next-session primero.")
        pg = Postgres(root, state["pg_bin"], read_json(root / "local.json"))
        pg.start()
        try:
            django_setup(root)
            from local_pos.models import LocalSaleSession, LocalCommand
            from local_pos.sessions import handoff_session
            from local_pos.sales import sync_cycle
            session = LocalSaleSession.objects.get(node_id=state["instance_id"])
            if LocalCommand.objects.filter(node_id=state["instance_id"]).exclude(state="accepted").exists():
                raise RuntimeError("Hay operaciones pendientes o en revisión. No se puede entregar la caja a otro cajero.")
            pending = root / "handoff-intent.json"
            if pending.exists():
                intent = read_json(pending)
            else:
                if not session.data.get("closed"):
                    raise RuntimeError("Primero confirma y sincroniza el cierre del cajero anterior.")
                intent = {"username": username, "previous": str(session.session_id), "requested": str(uuid4())}
                save_json(pending, intent)
            if intent["username"] != username:
                raise RuntimeError("Reanuda el relevo con el mismo usuario; no se reemplazó la transición pendiente.")
            binding = read_json(root / "replica-connection.json")
            data = pairing_call(binding["url"], "session", {"username": username, "password": password,
                "session_id": intent["requested"]}, credential=binding)
            new_binding = {**binding, "session_id": intent["requested"]}
            ReplicaRemote(new_binding)
            save_json(root / "replica-connection.json", new_binding)
            actor = handoff_session(data, previous_id=intent["previous"], requested_id=intent["requested"],
                                    username=username, local_password=local_password)
            state["local_user_id"] = actor.pk
            save_json(root / "installation.json", state)
            sync_cycle(ReplicaRemote(new_binding), local_user_id=actor.pk)
            pending.unlink()
            local_username = actor.nombreusuario
        finally:
            from django.db import connections
            connections.close_all()
            pg.stop()
    print(f"Relevo confirmado. Inicia el POS y entra como {local_username} con su contraseña LOCAL. Se conservaron todos los autores anteriores.")


def backup(root, target):
    root, target = Path(root).absolute(), Path(target).absolute()
    state = installed_state(root)
    if target.exists() or root == target or root in target.parents:
        raise RuntimeError("El respaldo debe ser un archivo nuevo fuera de la instalación.")
    private_dir(target.parent)
    with installation_lock(root):
        pg = Postgres(root, state["pg_bin"], read_json(root / "local.json"))
        if pg.status():
            raise RuntimeError("Cierra completamente el POS antes de respaldar.")
        _backup_locked(root, target, state)
    print("Respaldo privado creado. Contiene credenciales: no lo compartas ni lo subas a Git.")


def _backup_locked(root, target, state):
    """Requiere bloqueo exclusivo y PostgreSQL detenido; también usado por upgrade."""
    paths = [p for p in root.rglob("*") if p.is_file() and p.name != "runtime.lock"]
    if any(p.is_symlink() for p in root.rglob("*")):
        raise RuntimeError("No se respaldan instalaciones que contengan enlaces.")
    info = {"format": FORMAT + "-backup", "original_root": str(root), "system": platform.system(),
            "instance_id": state["instance_id"], "package_id": state["package_id"],
            "directories": sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_dir()),
            "files": {p.relative_to(root).as_posix(): _file_hash(p) for p in paths}}
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as file:
        with zipfile.ZipFile(file, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("backup-manifest.json", json.dumps(info))
            for path in paths:
                archive.write(path, path.relative_to(root).as_posix())
        file.flush()
        os.fsync(file.fileno())


def _unpack_backup(target, backup_path, *, confirm_instance, original_root, package_id):
    target = Path(target).absolute()
    if target.exists():
        raise RuntimeError("La recuperación nunca sobrescribe datos. El directorio original debe estar ausente.")
    with zipfile.ZipFile(backup_path) as archive:
        members = archive.infolist()
        if len(members) > 100_000 or sum(item.file_size for item in members) > 30 * 1024 ** 3:
            raise RuntimeError("Respaldo fuera de los límites de recuperación.")
        names = [item.filename for item in members]
        if len(set(names)) != len(names):
            raise RuntimeError("El respaldo contiene rutas duplicadas.")
        info = json.loads(archive.read("backup-manifest.json"))
        if (info.get("format") != FORMAT + "-backup" or info.get("system") != platform.system()
                or info.get("instance_id") != str(UUID(confirm_instance))
                or info.get("original_root") != str(original_root)
                or info.get("package_id") != package_id):
            raise RuntimeError("Recuperación incompatible: solo el mismo equipo/ruta/sistema/versión e identidad confirmada.")
        expected = info.get("files", {})
        directories = info.get("directories")
        if (not isinstance(directories, list) or len(directories) > 100_000
                or any(not isinstance(name, str) or not name for name in directories)
                or len(set(directories)) != len(directories)):
            raise RuntimeError("El respaldo no incluye un inventario válido de directorios.")
        if set(names) != set(expected) | {"backup-manifest.json"}:
            raise RuntimeError("El inventario del respaldo no coincide.")
        for name in directories:
            path = Path(name)
            if (not path.parts or path.anchor or ".." in path.parts or "\\" in name or ":" in name
                    or name in expected or any(parent.as_posix() in expected for parent in path.parents)):
                raise RuntimeError("Directorio inseguro en el respaldo.")
        for item in members:
            path = Path(item.filename)
            if (path.anchor or ".." in path.parts or "\\" in item.filename or ":" in item.filename
                    or (item.external_attr >> 16) & 0o170000 == 0o120000):
                raise RuntimeError("Ruta insegura en el respaldo.")
            if item.filename != "backup-manifest.json":
                with archive.open(item) as stream:
                    if _stream_hash(stream) != expected[item.filename]:
                        raise RuntimeError("El respaldo está dañado; no se recuperó nada.")
        private_dir(target)
        # PostgreSQL requiere también sus carpetas vacías (pg_notify, pg_tblspc,
        # etc.). Respaldar solo archivos crea una copia que no puede arrancar.
        for name in directories:
            (target / name).mkdir(parents=True, exist_ok=True, mode=0o700)
        for item in members:
            if item.filename == "backup-manifest.json":
                continue
            path = target / item.filename
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                with archive.open(item) as source:
                    shutil.copyfileobj(source, stream, 1024 * 1024)
                stream.flush()
                os.fsync(stream.fileno())
    return info


def restore(target, backup_path, *, confirm_instance):
    target = Path(target).absolute()
    _unpack_backup(target, backup_path, confirm_instance=confirm_instance,
                   original_root=target, package_id=package_manifest()["package_id"])
    installed_state(target)
    print("Respaldo recuperado en su ubicación original. No ejecutes ninguna copia antigua de esta identidad.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--pg-bin", required=True, type=Path)
    init.add_argument("--http-port", type=int, default=8910)
    init.add_argument("--pg-port", type=int, default=55441)
    init.add_argument("--username", required=True)
    sub.add_parser("serve")
    sub.add_parser("status")
    pairing = sub.add_parser("pair")
    pairing.add_argument("--url", required=True)
    pairing.add_argument("--username", required=True)
    renewal = sub.add_parser("next-session")
    renewal.add_argument("--username", required=True)
    handoff = sub.add_parser("switch-user")
    handoff.add_argument("--username", required=True)
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("--output", required=True, type=Path)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("--backup", required=True, type=Path)
    restore_parser.add_argument("--confirm-instance", required=True)
    upgrade_parser = sub.add_parser("upgrade")
    upgrade_parser.add_argument("--previous-package", required=True, type=Path)
    upgrade_parser.add_argument("--backup-dir", required=True, type=Path)
    rollback_parser = sub.add_parser("rollback-upgrade")
    rollback_parser.add_argument("--backup-dir", required=True, type=Path)
    rollback_parser.add_argument("--confirm-instance", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.command == "init":
            password = getpass.getpass("Contraseña LOCAL nueva (mínimo 12 caracteres): ")
            if password != getpass.getpass("Repite la contraseña LOCAL: "):
                raise RuntimeError("Las contraseñas no coinciden.")
            initialize(args.data_dir, args.pg_bin, http_port=args.http_port, pg_port=args.pg_port,
                       username=args.username, password=password)
        elif args.command == "serve":
            serve(args.data_dir)
        elif args.command == "pair":
            pair(args.data_dir, args.url, code=getpass.getpass("Código de un solo uso del Web Master: "),
                 username=args.username, password=getpass.getpass("Contraseña de ese usuario en la NUBE: "))
        elif args.command == "next-session":
            next_session(args.data_dir, username=args.username, password=getpass.getpass("Contraseña del mismo usuario en la NUBE: "))
        elif args.command == "switch-user":
            password = getpass.getpass("Contraseña del nuevo cajero en la NUBE: ")
            local_password = getpass.getpass("Contraseña LOCAL distinta (mínimo 12 caracteres): ")
            if local_password != getpass.getpass("Repite la contraseña LOCAL: "):
                raise RuntimeError("Las contraseñas locales no coinciden.")
            switch_user(args.data_dir, username=args.username, password=password, local_password=local_password)
        elif args.command == "backup":
            backup(args.data_dir, args.output)
        elif args.command == "restore":
            restore(args.data_dir, args.backup, confirm_instance=args.confirm_instance)
        elif args.command == "upgrade":
            from .upgrades import upgrade
            upgrade(args.data_dir, previous_package=args.previous_package, backup_dir=args.backup_dir)
        elif args.command == "rollback-upgrade":
            from .upgrades import rollback
            rollback(args.data_dir, backup_dir=args.backup_dir, confirm_instance=args.confirm_instance)
        else:
            state = installed_state(args.data_dir)
            pg = Postgres(args.data_dir, state["pg_bin"], read_json(args.data_dir / "local.json"))
            print(json.dumps({"instance_id": state["instance_id"], "ready": True, "postgres_running": pg.status(),
                "url": f"http://127.0.0.1:{state['http_port']}/", "paired": (args.data_dir / "replica-connection.json").is_file(),
                "production_certified": False, "authorization": "La sesión y copia deben seguir vigentes; no se renuevan por reiniciar."}))
    except KeyboardInterrupt:
        print("POS detenido; los datos persistentes se conservaron.")
    except Exception as exc:
        # Do not render SQL, environment, token or server response bodies.
        from pos_shared.protocol import ProtocolError
        if isinstance(exc, (RuntimeError, ProtocolError)):
            print(str(exc), file=sys.stderr)
        else:
            print("No se completó la operación. Los archivos existentes se conservaron; solicita revisión técnica.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
