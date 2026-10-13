from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from local_pos.integrated_config import config_path, load_config
from mainApp.services.hybrid_activation_checks import ActivationCheckError, inspect_source


class Command(BaseCommand):
    help = "Comprueba requisitos de activación sin activar, copiar datos ni modificar la nube."
    requires_system_checks = []
    requires_migrations_checks = False

    def add_arguments(self, parser):
        users = parser.add_mutually_exclusive_group(required=True)
        users.add_argument("--usuario", help="Nombre completo exacto o nombre de acceso.")
        users.add_argument("--usuario-id", type=int)
        points = parser.add_mutually_exclusive_group(required=True)
        points.add_argument("--caja", help="Nombre exacto dentro de la sucursal del usuario.")
        points.add_argument("--caja-id", type=int)
        parser.add_argument("--config", type=Path)
        parser.add_argument("--comprobar-motor", action="store_true",
                            help="También inicia y prueba el motor privado; lo detiene al terminar.")

    def handle(self, *args, **options):
        path = options["config"] or config_path(settings.BASE_DIR)
        try:
            config = load_config(path, settings.BASE_DIR)
        except (OSError, RuntimeError, ValueError) as exc:
            message = str(exc) if isinstance(exc, RuntimeError) else "No se pudo validar la configuración privada."
            raise CommandError(message) from None
        self.stdout.write(f"Modo actual: {config.get('activation', 'integrated_preview')}; PostgreSQL local: puerto {config['port']}.")
        if options["comprobar_motor"]:
            call_command("diagnosticar_postgres_local", config=path, stdout=self.stdout, stderr=self.stderr)
        try:
            result = inspect_source(options["usuario"], options["caja"],
                                    user_id=options["usuario_id"], point_id=options["caja_id"])
        except ActivationCheckError as exc:
            raise CommandError(f"Activación pendiente: {exc} No se cambió la configuración ni se guardó una venta.") from None
        self.stdout.write(self.style.SUCCESS(
            f"Requisitos de nube correctos: {result['user_name']} (usuario {result['user_id']}), "
            f"{result['point_name']} (caja {result['point_id']}), sucursal {result['branch_name']}."))
        self.stdout.write(f"Turno: {result['turn_id'] if result['required_turn'] else 'no exigido por la configuración del negocio'}.")
        self.stdout.write("Esto NO activa ventas offline. Falta respaldar/revisar la copia local y realizar la activación supervisada.")
