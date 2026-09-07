import time

from django.core.management.base import BaseCommand
from django.db import DatabaseError, close_old_connections

from mainApp.services.feature_flags import TELEGRAM_BOT_FEATURE, is_feature_enabled
from mainApp.services.telegram_bot import process_next_update, recover_stale_updates


class Command(BaseCommand):
    help = "Procesa de forma continua la cola segura del bot inteligente de Telegram."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Procesa como máximo un mensaje y termina.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=2.0,
            help="Segundos de espera cuando la cola está vacía (predeterminado: 2).",
        )
        parser.add_argument(
            "--max",
            type=int,
            default=0,
            dest="max_messages",
            help="Cantidad máxima a procesar; 0 significa sin límite.",
        )

    def handle(self, *args, **options):
        once = bool(options["once"])
        sleep_seconds = min(max(float(options["sleep"]), 0.2), 60.0)
        maximum = max(int(options["max_messages"]), 0)
        processed = 0
        self.stdout.write("Procesador del bot de Telegram iniciado.")
        try:
            recover_stale_updates()
            last_recovery = time.monotonic()
            while True:
                close_old_connections()
                if not is_feature_enabled(TELEGRAM_BOT_FEATURE, fresh=True):
                    if once:
                        self.stdout.write("El bot está desactivado; no se procesó ningún mensaje.")
                        break
                    time.sleep(max(sleep_seconds, 10.0))
                    continue
                try:
                    if time.monotonic() - last_recovery >= 60:
                        recover_stale_updates()
                        last_recovery = time.monotonic()
                    found = process_next_update()
                except DatabaseError as exc:
                    self.stderr.write(f"Base de datos no disponible: {exc}")
                    close_old_connections()
                    if once:
                        raise
                    time.sleep(min(max(sleep_seconds * 3, 5.0), 30.0))
                    continue
                if found:
                    processed += 1
                    if once or (maximum and processed >= maximum):
                        break
                    continue
                if once:
                    break
                time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            self.stdout.write("Procesador detenido.")
        finally:
            close_old_connections()
        self.stdout.write(self.style.SUCCESS(f"Mensajes atendidos en esta ejecución: {processed}."))
