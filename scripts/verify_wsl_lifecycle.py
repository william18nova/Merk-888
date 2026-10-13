"""Check the prepared development WSL engine, including a Windows parent crash.

Only temporary SQL objects and rollback are used. No business rows or cloud
connections are touched. The artificial child below is the only process killed.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def child():
    from local_pos.integrated_bootstrap import runserver_context
    from local_pos.integrated_config import config_path, load_config, local_database
    from django.conf import settings
    # Deliberately never import NovaSoft.settings or an Aiven configuration.
    config = load_config(config_path(ROOT), ROOT)
    settings.configure(DATABASES={"default": local_database(config)}, INSTALLED_APPS=[],
        SECRET_KEY="isolated-lifecycle-verification", USE_TZ=True)
    import django
    django.setup()
    with runserver_context(["manage.py", "runserver"], ROOT):
        assert os.environ["DJANGO_SETTINGS_MODULE"] == "NovaSoft.wsl_development_settings"
        from django.db import connection, transaction
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("CREATE TEMP TABLE nova_lifecycle(value integer)")
                cursor.execute("DO $$ BEGIN INSERT INTO nova_lifecycle VALUES (42); END $$ LANGUAGE plpgsql")
                cursor.execute("SELECT value FROM nova_lifecycle")
                assert cursor.fetchone()[0] == 42
            transaction.set_rollback(True)
        print("READY: Windows Django SQL + PL/pgSQL; no cloud settings loaded", flush=True)
        sys.stdin.readline()


def run():
    from local_pos.integrated_config import config_path, load_config
    from local_pos.wsl_postgres import WslPostgres
    config = load_config(config_path(ROOT), ROOT)
    pg = WslPostgres(config["data_dir"], config["pg_bin"], config)
    if pg.status():
        raise RuntimeError("Cierra runserver antes de verificar el ciclo privado.")
    env = {k: v for k, v in os.environ.items() if k not in {"DJANGO_SETTINGS_MODULE", "RUN_MAIN"}}
    env["PYTHONIOENCODING"] = "utf-8"
    for crash in (False, True, False):
        process = subprocess.Popen([sys.executable, "-B", __file__, "--child"], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            # Bounded wait without blocking on an unavailable pipe indefinitely.
            import queue
            import threading
            output = queue.Queue()
            threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True).start()
            line = output.get(timeout=90)
            if not line.startswith("READY:"):
                remaining, _ = process.communicate(timeout=65)
                raise RuntimeError("El proceso de prueba no inició: " + line + remaining)
            print(line.strip(), flush=True)
            if crash:
                process.kill()  # Our own artificial Python child, never PostgreSQL.
            else:
                process.stdin.write("stop\n")
                process.stdin.flush()
            process.wait(timeout=65)
            if not crash and process.returncode:
                raise RuntimeError(process.stdout.read())
            deadline = time.monotonic() + 60
            while pg.status():
                if time.monotonic() >= deadline:
                    raise RuntimeError("El supervisor no cerró su propio PostgreSQL.")
                time.sleep(0.3)
            print("PASS: " + ("forced Windows parent exit -> supervised PostgreSQL shutdown" if crash else
                "normal startup/shutdown, persistent configuration reused"), flush=True)
        finally:
            if process.poll() is None:
                process.stdin.close()
                try:
                    process.wait(timeout=65)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
    print("PASS: restart after parent crash; development database preserved and stopped.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args()
    child() if args.child else run()
