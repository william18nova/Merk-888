import json
import os
from pathlib import Path
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from local_pos.replica import synchronize
from local_pos.replica_transport import ReplicaRemote


class Command(BaseCommand):
    help = "Actualiza la referencia local; con ventas habilitadas también envía su cola autorizada."

    def add_arguments(self, parser):
        parser.add_argument("--connection-file", required=True)
        parser.add_argument("--local-user", type=int, required=True)
        parser.add_argument("--watch", action="store_true")

    def handle(self, *args, **options):
        path = Path(options["connection_file"])
        if (not path.is_absolute() or path.is_symlink() or not path.is_file()
                or path.parent.resolve() != Path(settings.LOCAL_CONFIG["data_dir"]).resolve()):
            raise CommandError("La vinculación debe estar en el directorio privado de esta instalación.")
        if os.name == "posix" and path.stat().st_mode & 0o077:
            raise CommandError("El archivo de vinculación debe ser privado (chmod 600).")
        try:
            remote = ReplicaRemote(json.loads(path.read_text(encoding="utf-8")),
                allow_local=settings.HYBRID_LOCAL_MODE == "development_readonly")
        except (ValueError, OSError):
            raise CommandError("No se pudo leer una vinculación válida.") from None
        delay = 30
        while True:
            try:
                if getattr(settings, "HYBRID_LOCAL_SALES_ENABLED", False):
                    from local_pos.sales import sync_cycle
                    state = sync_cycle(remote, local_user_id=options["local_user"])
                else:
                    state = synchronize(remote, local_user_id=options["local_user"])
                self.stdout.write(f"Copia de referencia actualizada: {state.active_id}")
                delay = 30
            except Exception as exc:
                # La salida no incluye claves ni contenido de registros.
                self.stderr.write("No se completó la actualización; consulta el estado local. La copia anterior se conserva.")
                if not options["watch"] or getattr(exc, "status", None) in (401, 403):
                    raise CommandError("Sincronización de referencia detenida.") from None
                delay = min(delay * 2, 300)
            if not options["watch"]:
                return
            try:
                time.sleep(delay)
            except KeyboardInterrupt:
                return
