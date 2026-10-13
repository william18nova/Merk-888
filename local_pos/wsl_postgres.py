"""Windows client for a private Linux PostgreSQL; no native PostgreSQL DLLs."""
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time

from .runtime import port_free


def validate_backend(config):
    backend = config.get("backend")
    if not isinstance(backend, dict) or set(backend) != {"kind", "distribution", "user", "data_dir"}:
        raise RuntimeError("Configuración del motor WSL inválida.")
    if backend["kind"] != "wsl" or any(not isinstance(backend[key], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", backend[key])
            for key in ("distribution", "user")) or backend["user"] == "root":
        raise RuntimeError("Distribución o usuario WSL inválidos.")
    expected = PurePosixPath("/home") / backend["user"] / ".local/share/NovaPOS/integrated" / config["instance_id"]
    if backend["data_dir"] != str(expected):
        raise RuntimeError("WSL requiere el directorio privado de esta instancia dentro del home de Linux.")
    if not re.fullmatch(r"/usr/lib/postgresql/[1-9][0-9]*/bin", config["pg_bin"]):
        raise RuntimeError("WSL requiere los binarios PostgreSQL Linux instalados en /usr/lib/postgresql.")


def wait_for_forwarding_release(port, timeout=20):
    # WSL's Windows localhost relay may outlive the Linux server briefly. Wait
    # for that port to become truly free; never kill an owner or pick a new DB.
    deadline = time.monotonic() + timeout
    while True:
        try:
            port_free(port)
            return
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


class WslPostgres:
    def __init__(self, root, pg_bin, config):
        if os.name != "nt":
            raise RuntimeError("Este conector WSL se inicia desde Windows.")
        validate_backend(config)
        self.root, self.config = Path(root), config
        self.process = self.log = None
        # Use the trusted system executable, not an executable injected in PATH.
        self.wsl = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/wsl.exe")
        self.prefix = [self.wsl, "--distribution", config["backend"]["distribution"],
                       "--user", config["backend"]["user"], "--exec"]
        result = subprocess.run([*self.prefix, "wslpath", "-a", "-u", str(Path(__file__).with_name("wsl_postgres_worker.py").resolve())],
            capture_output=True, text=True, encoding="utf-8", check=True, timeout=60)
        self.worker = result.stdout.strip()
        if not self.worker.startswith("/") or "\n" in self.worker:
            raise RuntimeError("No se pudo localizar el supervisor de PostgreSQL en WSL.")

    def payload(self, **extra):
        # Never forward cloud or regular application credentials to the supervisor.
        return {key: self.config[key] for key in ("backend", "instance_id", "pg_bin", "port")} | extra

    def command(self, action):
        return [*self.prefix, "python3", "-B", self.worker, action]

    def run_worker(self, action, **extra):
        return subprocess.run(self.command(action), input=json.dumps(self.payload(**extra)) + "\n",
            capture_output=True, text=True, encoding="utf-8", timeout=90)

    def initialize(self, password):
        result = self.run_worker("init", bootstrap_password=password)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "Falló la inicialización privada en WSL.")

    def status(self):
        result = self.run_worker("status")
        if result.returncode not in (0, 3):
            raise RuntimeError(result.stderr.strip() or "No se pudo consultar el clúster WSL.")
        return result.returncode == 0

    def start(self, *, initializing_password=None):
        if self.process is not None:
            raise RuntimeError("Este supervisor ya está iniciado.")
        wait_for_forwarding_release(self.config["port"])
        self.log = (self.root / "wsl-runtime.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(self.command("serve"), stdin=subprocess.PIPE,
            stdout=self.log, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.process.stdin.write(json.dumps(self.payload()) + "\n")
            self.process.stdin.flush()
            import psycopg2
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(f"No arrancó PostgreSQL en WSL. Revisa {self.root / 'wsl-runtime.log'}.")
                try:
                    connection = psycopg2.connect(host="127.0.0.1", port=self.config["port"],
                        dbname="postgres" if initializing_password else self.config["database"],
                        user="nova_integrated_owner" if initializing_password else self.config["user"],
                        password=initializing_password or self.config["password"], connect_timeout=1)
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT version()")
                            if "linux" not in cursor.fetchone()[0].lower():
                                raise RuntimeError("El servidor de destino no es PostgreSQL Linux.")
                    finally:
                        connection.close()
                    return
                except psycopg2.OperationalError:
                    time.sleep(0.25)
            raise RuntimeError("Windows no pudo conectar con PostgreSQL de WSL por localhost. Revisa el registro y localhostForwarding; no se cambiará el firewall.")
        except BaseException:
            try:
                self.stop()
            except RuntimeError:
                pass  # Preserve the original startup diagnostic.
            raise

    def stop(self):
        if self.process is None:
            return
        result = None
        try:
            if self.process.stdin:
                try:
                    self.process.stdin.close()
                except BrokenPipeError:
                    pass
            result = self.process.wait(timeout=60)
        finally:
            if self.log:
                self.log.close()
                self.log = None
            if self.process.poll() is not None:
                self.process = None
        if result:
            raise RuntimeError(f"El supervisor WSL terminó con error; revisa {self.root / 'wsl-runtime.log'}.")
