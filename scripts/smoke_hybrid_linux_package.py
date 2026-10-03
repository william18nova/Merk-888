"""Prueba un paquete Linux en un HOME temporal, sin datos ni impresoras reales."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import ProxyHandler, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("executable", type=Path)
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("Esta comprobación del instalador está diseñada para Linux.")
    executable = args.executable.resolve()
    manifest = json.loads((executable.parent / "SHA256.json").read_text())
    for name, expected in manifest.items():
        path = (executable.parent / name).resolve()
        if not path.is_relative_to(executable.parent):
            raise AssertionError("Ruta fuera del paquete")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name
    with tempfile.TemporaryDirectory(prefix="novapos-install-test-") as directory:
        home = Path(directory)
        env = {**os.environ, "HOME": str(home), "XDG_DATA_HOME": str(home / "data")}
        result = subprocess.run([str(executable), "--install"], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        programs = list((home / ".local/share/novapos-app/versions").glob("*/NovaPOS"))
        assert len(programs) == 1
        program = programs[0]
        assert (home / ".local/share/applications/novapos.desktop").is_file()
        # El instalador no debe sobrescribir una versión ya instalada.
        repeated = subprocess.run([str(executable), "--install"], env=env, capture_output=True, timeout=30)
        assert repeated.returncode != 0
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with (home / "process.log").open("w") as log:
            process = subprocess.Popen([str(program), "--no-browser", "--port", str(port)], env=env, stdout=log, stderr=log)
            try:
                opener = build_opener(ProxyHandler({}))
                deadline = time.monotonic() + 15
                while True:
                    try:
                        with opener.open(f"http://127.0.0.1:{port}/api/status", timeout=1) as response:
                            status = json.load(response)
                        break
                    except OSError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError("El ejecutable no inició")
                        time.sleep(0.1)
                assert status["version"] == program.parent.name
                assert not status["paired"] and not status["unlocked"]
                with opener.open(f"http://127.0.0.1:{port}/", timeout=2) as response:
                    assert b"printer-dialog" in response.read()
                assert (home / "data/novapos/operations.sqlite3").is_file()
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        print("OK: hashes, instalación Linux aislada, acceso directo, no sobrescritura, ejecutable y servidor local.")


if __name__ == "__main__":
    main()
