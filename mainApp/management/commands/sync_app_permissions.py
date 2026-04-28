from django.core.management.base import BaseCommand

from mainApp.permissions import (
    grant_all_permissions_to_web_master,
    permission_catalog,
    sync_permission_catalog,
)


class Command(BaseCommand):
    help = "Crea en la tabla permisos los permisos usados por rutas y navbar."

    def handle(self, *args, **options):
        created = sync_permission_catalog()
        granted = grant_all_permissions_to_web_master()
        total = len(permission_catalog())
        self.stdout.write(
            self.style.SUCCESS(
                "Catalogo de permisos sincronizado. "
                f"Nuevos: {created}. Total esperado: {total}. "
                f"Permisos nuevos para Web Master: {granted}."
            )
        )
