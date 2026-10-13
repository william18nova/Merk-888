from pathlib import Path
import subprocess

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from local_pos.integrated_config import default_path
from local_pos.integrated_prepare import prepare


class Command(BaseCommand):
    help = "Prepara PostgreSQL privado para el runserver integrado (todavía sin copia de datos del negocio)."
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        parser.add_argument("--pg-bin", required=True)
        parser.add_argument("--port", type=int, default=55460)
        parser.add_argument("--config", type=Path)
        parser.add_argument("--wsl-distribution", help="Solo desarrollo Windows: distribución WSL existente.")
        parser.add_argument("--wsl-user", help="Usuario Linux sin privilegios; no root.")

    def handle(self, *args, **options):
        path = options["config"] or default_path(settings.BASE_DIR)
        try:
            prepare(settings.BASE_DIR, path, options["pg_bin"], port=options["port"],
                    wsl_distribution=options["wsl_distribution"], wsl_user=options["wsl_user"])
        except (RuntimeError, OSError) as exc:
            raise CommandError(str(exc)) from None
        except subprocess.SubprocessError:
            raise CommandError("La preparación no terminó. Revisa el error de PostgreSQL mostrado arriba; "
                               "se conservaron los archivos y no se activó la instalación. "
                               "No desactives las protecciones de Windows para continuar.") from None
        self.stdout.write(self.style.SUCCESS("PostgreSQL privado preparado. El comando habitual runserver puede iniciarlo."))
        if options["wsl_distribution"]:
            self.stdout.write("Modo desarrollo: Aiven sigue como base principal; WSL queda disponible como alias offline. No cambia la autenticación ni las ventas actuales.")
        self.stdout.write("Aún falta vincular la copia autorizada y la cola; NO habilita ventas offline por sí solo.")
