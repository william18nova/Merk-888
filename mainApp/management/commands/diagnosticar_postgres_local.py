"""Safe repeatable diagnostic; no access to the configured cloud database."""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from local_pos.integrated_config import config_path, load_config
from local_pos.runtime import installation_lock, Postgres


class Command(BaseCommand):
    help = "Comprueba arranque, SQL y PL/pgSQL del PostgreSQL privado, sin acceder a Aiven."
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("--config", type=Path)

    def handle(self, *args, **options):
        path = options["config"] or config_path(settings.BASE_DIR)
        try:
            config = load_config(path, settings.BASE_DIR)
            if "backend" in config:
                from local_pos.wsl_postgres import WslPostgres
                engine = WslPostgres
            else:
                engine = Postgres
            with installation_lock(Path(config["data_dir"])):
                postgres = engine(config["data_dir"], config["pg_bin"], config)
                postgres.start()
                try:
                    import psycopg2
                    connection = psycopg2.connect(host="127.0.0.1", port=config["port"],
                        dbname=config["database"], user=config["user"], password=config["password"],
                        connect_timeout=3, options="-c statement_timeout=10000")
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute("SELECT version()")
                            self.stdout.write(cursor.fetchone()[0])
                            cursor.execute("CREATE TEMP TABLE nova_diagnostic(value integer)")
                            cursor.execute("DO $$ BEGIN INSERT INTO nova_diagnostic VALUES (42); END $$ LANGUAGE plpgsql")
                            cursor.execute("UPDATE nova_diagnostic SET value=value+1 RETURNING value")
                            if cursor.fetchone()[0] != 43:
                                raise RuntimeError("La comprobación de escritura no coincidió.")
                            cursor.execute("SELECT value FROM nova_diagnostic")
                            if cursor.fetchone()[0] != 43:
                                raise RuntimeError("La comprobación de lectura no coincidió.")
                            cursor.execute("DELETE FROM nova_diagnostic RETURNING value")
                            if cursor.fetchone()[0] != 43:
                                raise RuntimeError("La comprobación de borrado no coincidió.")
                        connection.rollback()
                    finally:
                        connection.close()
                finally:
                    postgres.stop()
        except Exception as exc:
            # Driver errors can carry connection details: only deliberate local
            # validation messages are displayed. No settings/password dumps.
            message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
            raise CommandError(f"Diagnóstico local incompleto: {message}") from None
        self.stdout.write(self.style.SUCCESS(f"OK: Windows/cliente -> 127.0.0.1:{config['port']}; PL/pgSQL y lectura/escritura. Transacción revertida y motor privado detenido."))
