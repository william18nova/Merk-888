"""Own the private PostgreSQL process for a normal manage.py runserver."""
from contextlib import contextmanager
import os
from pathlib import Path

from .integrated_config import config_path, load_config
from .runtime import installation_lock, Postgres


@contextmanager
def runserver_context(argv, project):
    # Never alter migrate, shell, tests, collectstatic, WSGI or explicit settings.
    if (len(argv) < 2 or argv[1] != "runserver"
            or any(arg == "--settings" or arg.startswith("--settings=") for arg in argv)
            or os.environ.get("DJANGO_SETTINGS_MODULE") not in {None, "NovaSoft.settings"}):
        yield
        return
    path = config_path(project)
    if not path.exists():
        if "NOVA_INTEGRATED_CONFIG" in os.environ or (Path(project) / ".novapos-runtime.json").exists():
            raise RuntimeError("No existe la configuración integrada indicada.")
        yield
        return
    config = load_config(path, project)
    addresses = [arg for arg in argv[2:] if not arg.startswith("-")]
    if any(not arg.isdigit() and not arg.startswith(("127.0.0.1:", "localhost:", "[::1]:")) for arg in addresses):
        raise RuntimeError("El POS integrado debe escuchar solo en localhost. Cada equipo conserva su propia copia privada.")
    os.environ["NOVA_INTEGRATED_CONFIG"] = str(path)
    os.environ["DJANGO_SETTINGS_MODULE"] = ("NovaSoft.wsl_development_settings"
        if config.get("activation") == "database_only" else "NovaSoft.integrated_settings")
    # Django's autoreload parent owns PostgreSQL. The child uses that process;
    # a second manually started server cannot take the same installation lock.
    if os.environ.get("RUN_MAIN") == "true":
        yield
        return
    root = Path(config["data_dir"])
    with installation_lock(root):
        if "backend" in config:
            from .wsl_postgres import WslPostgres
            postgres = WslPostgres(root, config["pg_bin"], config)
        else:
            postgres = Postgres(root, config["pg_bin"], config)
        started = False
        try:
            postgres.start()
            started = True
            yield
        finally:
            if started:
                # The local engine is ours, never the global PostgreSQL service.
                try:
                    from django.db import connections
                    connections.close_all()
                finally:
                    postgres.stop()
