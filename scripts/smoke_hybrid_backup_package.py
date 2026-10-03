"""Ensayo del respaldo del ejecutable distribuido, sin datos ni servicios reales."""
import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request, ProxyHandler, build_opener
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hybrid_client.backup import read_backup, restore_for_review, summary, write_new
from hybrid_client.client import Client
from hybrid_client.store import Store
from hybrid_client.test_client import FakeCloud

PASSWORD = "solo-pruebas-no-es-una-clave-real"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    parser.add_argument("--cross-platform-output", type=Path)
    parser.add_argument("--cross-platform-input", type=Path)
    args = parser.parse_args()
    program = str(args.executable.resolve())
    with tempfile.TemporaryDirectory(prefix="novapos-backup-smoke-") as folder:
        folder = Path(folder)
        store = Store(folder / "local")
        cloud = FakeCloud()
        client = Client(store, transport=cloud)
        client.enroll({"url": "https://example.test", "code": "fake"})
        client.start({"username": "demo", "password": "not-real", "pin": "demo-local"})
        store.set("config", {**store.get("config"), "url": "https://127.0.0.1:1"})
        cloud.offline = True
        client.checkout({"operation_id": str(uuid4()), "items": [{"id": 10, "quantity": 500}], "cash_received": "2000"})
        backup = args.cross_platform_output or folder / "demo.novabackup"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        opener = build_opener(ProxyHandler({}))
        base = f"http://127.0.0.1:{port}"
        with (folder / "package.log").open("w") as log:
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
            process = subprocess.Popen([program, "--data-dir", str(store.directory), "--port", str(port), "--no-browser"], stdout=log, stderr=log, **options)
            try:
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with opener.open(base + "/", timeout=1) as response:
                            html = response.read().decode()
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError("El paquete no arrancó")
                        time.sleep(.1)
                token = re.search(r'name="local-token" content="([^"]+)"', html)[1]
                def post(path, data):
                    with opener.open(Request(base+path, data=json.dumps(data).encode(), headers={"Content-Type": "application/json", "Origin": base, "X-Local-Token": token}), timeout=10) as response:
                        return response.read()
                post("/api/unlock", {"pin": "demo-local"})
                raw = post("/api/backup", {"session_id": store.get("session")["session_id"], "pin": "demo-local", "password": PASSWORD, "confirmation": PASSWORD})
                write_new(backup, raw)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        document = read_backup(backup, PASSWORD)
        restore_for_review(document, folder / "review")
        assert summary(document)["pending"] == 1
        # Importa la recuperación desde el ejecutable, sin pedir contraseñas ni
        # contactar servidores. Una ruta ausente debe fallar de forma controlada.
        unavailable = subprocess.run([program, "--resume-recovery", str(folder / "not-restored")],
                                     capture_output=True, timeout=10)
        assert unavailable.returncode == 1
        assert b"ModuleNotFoundError" not in unavailable.stderr
        assert b"Conserva el respaldo" in unavailable.stderr
        if args.cross_platform_input:
            imported = read_backup(args.cross_platform_input, PASSWORD)
            restore_for_review(imported, folder / "cross-review")
            assert summary(imported)["pending"] == 1
        try:
            Store(folder / "review")
        except RuntimeError:
            pass
        else:
            raise AssertionError("No debe poder activarse una copia como segunda caja")
        assert store.operation(client.store.pending()[0]["id"])["state"] == "pending"
        assert cloud.counter == 0
    print("OK: respaldo exportado por el ejecutable, verificado y recuperado para revisión; pendientes conservados y reactivación bloqueada.")


if __name__ == "__main__":
    main()
