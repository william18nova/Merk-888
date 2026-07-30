from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from mainApp.services.product_price_sync import (
    ProductPriceSyncError,
    sync_product_prices,
)


SCHEDULED_WEEKDAYS = {1, 4, 6}  # martes, viernes y domingo


def should_run_today(day=None):
    day = day or timezone.localdate()
    return day.weekday() in SCHEDULED_WEEKDAYS


class Command(BaseCommand):
    help = (
        "Consulta los precios de Plaza y actualiza los productos mapeados en merk2. "
        "Sin --apply solo realiza una simulación."
    )

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument(
            "--apply",
            action="store_true",
            help="Aplica los cambios validados en merk2.",
        )
        mode.add_argument(
            "--dry-run",
            action="store_true",
            help="Ejecuta explícitamente la simulación (es el modo predeterminado).",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Ignora el calendario para una ejecución manual.",
        )

    def handle(self, *args, **options):
        local_day = timezone.localdate()
        if not options["force"] and not should_run_today(local_day):
            self.stdout.write(
                "Sincronización omitida: hoy no es martes, viernes ni domingo "
                f"en Colombia ({local_day.isoformat()})."
            )
            return

        apply_changes = bool(options["apply"])
        try:
            report = sync_product_prices(apply=apply_changes)
        except ProductPriceSyncError as exc:
            raise CommandError(str(exc)) from exc

        mode = "APLICADO" if report.applied else "SIMULACIÓN"
        self.stdout.write(
            f"{mode}: {report.mapping_count} mapeos, "
            f"{report.source_product_count} productos de origen, "
            f"{report.destination_product_count} productos de merk2, "
            f"{len(report.changes)} cambios y "
            f"{report.unchanged_count} sin cambio."
        )
        for change in report.changes:
            self.stdout.write(
                f"- {change.destination_id} {change.destination_name}: "
                f"${change.old_price} -> ${change.new_price} "
                f"(origen {change.source_id})"
            )

        if report.suspicious_changes:
            ids = ", ".join(
                str(change.destination_id)
                for change in report.suspicious_changes
            )
            self.stdout.write(
                self.style.WARNING(
                    "ADVERTENCIA: se detectaron variaciones extremas en "
                    f"{ids}. Una ejecución con --apply las bloqueará."
                )
            )
        elif not report.applied:
            self.stdout.write(
                self.style.WARNING(
                    "No se modificó ningún precio. Usa --apply únicamente "
                    "después de revisar esta simulación."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS("Precios actualizados correctamente.")
            )
