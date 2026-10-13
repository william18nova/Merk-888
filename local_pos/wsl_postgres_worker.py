"""Linux-only private PostgreSQL supervisor, invoked by Windows through WSL.

One JSON line arrives on stdin; credentials never appear in command arguments.
Keeping that pipe open keeps WSL alive. EOF (including a Windows parent crash)
shuts down ONLY the PostgreSQL child created by this supervisor.
"""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
from uuid import UUID


def paths(config, *, prepared=True):
    identity = str(UUID(config["instance_id"]))
    root = Path.home() / ".local/share/NovaPOS/integrated" / identity
    if config["backend"]["data_dir"] != str(root) or os.geteuid() == 0:
        raise RuntimeError("WSL: directorio o usuario privado incorrecto.")
    for path in (root, *root.parents):
        if path.is_symlink():
            raise RuntimeError("WSL: no se permiten enlaces en el directorio de datos.")
    binary = Path(config["pg_bin"])
    if not binary.is_absolute() or not (binary / "postgres").is_file():
        raise RuntimeError("WSL: PostgreSQL Linux no está instalado en esa ruta.")
    if prepared:
        marker = json.loads((root / "identity.json").read_text())
        if marker != {"instance_id": identity} or root.stat().st_uid != os.getuid():
            raise RuntimeError("WSL: identidad o propietario del clúster incorrecto.")
        if root.stat().st_mode & 0o077:
            raise RuntimeError("WSL: el directorio de datos no es privado.")
        version = subprocess.check_output([binary / "postgres", "--version"], text=True).split()[2].split(".")[0]
        if (root / "postgres/PG_VERSION").read_text().strip() != version:
            raise RuntimeError("WSL: cambió la versión mayor; se necesita una actualización con respaldo.")
    return root, binary


def initialize(config):
    root, binary = paths(config, prepared=False)
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.mkdir(mode=0o700)  # Never reuse/overwrite an existing directory.
    (root / "identity.json").write_text(json.dumps({"instance_id": config["instance_id"]}))
    password = root / "bootstrap-password"
    password.write_text(config["bootstrap_password"])
    try:
        with (root / "initdb.log").open("w") as log:
            result = subprocess.run([binary / "initdb", "-D", root / "postgres", "-U", "nova_integrated_owner",
                "-A", "scram-sha-256", "-E", "UTF8", "--locale=C", f"--pwfile={password}"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError(f"WSL: initdb falló; se conservó el diagnóstico en {root / 'initdb.log'}.")
    finally:
        password.unlink(missing_ok=True)


def serve(config):
    root, binary = paths(config)
    with (root / "supervisor.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if subprocess.run([binary / "pg_ctl", "-D", root / "postgres", "status"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            raise RuntimeError("WSL: el clúster ya está activo; no se tomará un proceso ajeno.")
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", config["port"]))
        with (root / "postgres.log").open("a") as log:
            process = subprocess.Popen([binary / "postgres", "-D", root / "postgres", "-h", "127.0.0.1",
                "-p", str(config["port"]), "-k", str(root), "-c", "max_connections=40"],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            disconnected = threading.Event()

            def watch_parent():
                try:
                    while sys.stdin.buffer.read(1):
                        pass
                finally:
                    disconnected.set()

            threading.Thread(target=watch_parent, daemon=True).start()
            try:
                while not disconnected.wait(0.2):
                    if process.poll() is not None:
                        raise RuntimeError(f"WSL: PostgreSQL terminó; revisa {root / 'postgres.log'}.")
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)  # PostgreSQL fast, transaction-safe shutdown.
                    try:
                        process.wait(timeout=45)
                    except subprocess.TimeoutExpired:
                        # Do not SIGKILL a database. Preserve it for diagnosis.
                        raise RuntimeError("WSL: PostgreSQL no confirmó su cierre; revisar el registro.") from None


def main():
    os.umask(0o077)
    config = json.loads(sys.stdin.buffer.readline(65536))
    action = sys.argv[1]
    if action == "probe":
        binary = Path(config["pg_bin"])
        if os.geteuid() == 0 or not binary.is_absolute():
            raise RuntimeError("Se requiere un usuario WSL sin privilegios y una ruta absoluta.")
        print(json.dumps({"home": str(Path.home()), "user": __import__('pwd').getpwuid(os.getuid()).pw_name,
            "version": subprocess.check_output([binary / "postgres", "--version"], text=True).strip(),
            "library": subprocess.check_output([binary / "pg_config", "--pkglibdir"], text=True).strip()}))
    elif action == "init":
        initialize(config)
    elif action == "serve":
        serve(config)
    elif action == "status":
        root, binary = paths(config)
        sys.exit(subprocess.run([binary / "pg_ctl", "-D", root / "postgres", "status"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode)
    else:
        raise RuntimeError("Operación WSL desconocida.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
