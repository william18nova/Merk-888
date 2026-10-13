"""Create only a private PostgreSQL schema; never clones or changes cloud data."""
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

from .integrated_config import FORMAT, DATABASE, schema_signature, private_config_path
from .runtime import Postgres, private_dir, installation_lock, save_json, port_free


def initialization_failure(stderr):
    text = str(stderr or '').lower()
    if '4551' in text:
        return ('Windows bloqueó un componente de PostgreSQL mediante Control de aplicaciones (4551). '
                'Revisa Code Integrity y usa una distribución compatible con sus políticas. '
                'No desactives Smart App Control, Defender ni otras protecciones.')
    if 'plpgsql' in text:
        return ('PostgreSQL no pudo cargar PL/pgSQL. Debes revisar el diagnóstico de inicialización '
                'para distinguir permisos, dependencias, instalación y políticas de Windows.')
    return 'No se pudo inicializar el PostgreSQL privado. Revisa su diagnóstico; no se sustituyeron bases existentes.'


def prepare(project, path, pg_bin, *, port=55460, wsl_distribution=None, wsl_user=None, integrated_preview=False, journal_preview=False):
    project = Path(project).resolve()
    path = private_config_path(path, project)
    root = path.parent
    if type(port) is not int or not 1024 <= port <= 65535:
        raise RuntimeError("Puerto local inválido.")
    if path.name != "config.json" or (root.exists() and any(root.iterdir())):
        raise RuntimeError("Se requiere config.json en una carpeta NUEVA. No se sobrescribe una instalación existente.")
    port_free(port)
    root = private_dir(root)
    config = {"format": FORMAT, "phase": "initializing", "project": str(project), "data_dir": str(root),
              "instance_id": str(uuid4()), "pg_bin": str(Path(pg_bin).absolute()), "port": port,
              "database": DATABASE, "user": DATABASE, "password": secrets.token_hex(32),
              "secret_key": secrets.token_hex(48), "schema_signature": schema_signature(project)}
    if wsl_distribution or wsl_user:
        from .wsl_postgres import WslPostgres
        config["pg_bin"] = str(pg_bin)
        config["backend"] = {"kind": "wsl", "distribution": wsl_distribution, "user": wsl_user,
            "data_dir": f"/home/{wsl_user}/.local/share/NovaPOS/integrated/{config['instance_id']}"}
        config["activation"] = "integrated_preview" if integrated_preview else "database_only"
        postgres = WslPostgres(root, config["pg_bin"], config)
    else:
        postgres = Postgres(root, config["pg_bin"], config)
    if journal_preview:
        config["activation"] = "journal_preview"
    save_json(path, config)
    with installation_lock(root):
        password_file = root / "bootstrap-password"
        password_file.write_text(secrets.token_hex(48), encoding="utf-8")
        if os.name == "posix":
            password_file.chmod(0o600)
        started = False
        try:
            if "backend" in config:
                postgres.initialize(password_file.read_text())
                postgres.start(initializing_password=password_file.read_text())
            else:
                try:
                    postgres.run("initdb", "-D", root / "postgres", "-U", "nova_integrated_owner", "-A", "scram-sha-256",
                                 "-E", "UTF8", "--locale=C", f"--pwfile={password_file}", capture=True)
                except subprocess.CalledProcessError as exc:
                    # initdb has no cloud credentials and only initializes a NEW
                    # cluster. Preserve its original error in the private folder.
                    log = root / "initialization-error.txt"
                    log.write_text((exc.stdout or '') + '\n' + (exc.stderr or ''), encoding='utf-8')
                    raise RuntimeError(initialization_failure(exc.stderr) + f' Diagnóstico: {log}') from None
                postgres.start()
            started = True
            import psycopg2
            from psycopg2 import sql
            connection = psycopg2.connect(host="127.0.0.1", port=port, dbname="postgres",
                user="nova_integrated_owner", password=password_file.read_text(), connect_timeout=3)
            try:
                connection.autocommit = True
                with connection.cursor() as cursor:
                    # Force-load PL/pgSQL BEFORE declaring this private installation
                    # prepared. A Windows DLL/policy failure must surface here, not
                    # during the cashier's first sale. Only this NEW cluster is used.
                    cursor.execute("DO $$ BEGIN IF 1 + 1 <> 2 THEN RAISE EXCEPTION 'PL/pgSQL'; END IF; END $$")
                    cursor.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD %s")
                        .format(sql.Identifier(DATABASE)), [config["password"]])
                    cursor.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(DATABASE), sql.Identifier(DATABASE)))
            finally:
                connection.close()
            env = {key: value for key, value in os.environ.items() if not key.startswith("PG")
                   and key not in {"DATABASE_URL", "DJANGO_SETTINGS_MODULE", "NOVA_LOCAL_CONFIG", "RUN_MAIN"}}
            env.update(DJANGO_SETTINGS_MODULE="NovaSoft.integrated_schema_settings", NOVA_INTEGRATED_CONFIG=str(path))
            code = ("import django; django.setup(); from django.core.management import call_command; "
                    "call_command('migrate', run_syncdb=True, interactive=False, verbosity=0); "
                    "from django.conf import settings; from local_pos.models import LocalNode; "
                    "LocalNode.objects.create(id=settings.LOCAL_CONFIG['instance_id'])")
            subprocess.run([sys.executable, "-B", "-c", code], cwd=project, env=env, check=True, timeout=180)
            config["phase"] = "prepared"
            save_json(path, config)
        finally:
            password_file.unlink(missing_ok=True)
            if started:
                postgres.stop()
    return config
