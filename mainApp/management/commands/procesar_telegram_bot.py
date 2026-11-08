import os
import math
import sys
import time
from contextlib import ExitStack

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import DatabaseError, close_old_connections, connection

from mainApp.services.feature_flags import TELEGRAM_BOT_FEATURE, is_feature_enabled
from mainApp.services.telegram_bot import process_next_update, recover_stale_updates
from mainApp.services.telegram_runtime import SourceWatcher
from mainApp.services.telegram_transport import pooled_http_session


class Command(BaseCommand):
    help = "Procesa de forma continua la cola segura del bot inteligente de Telegram."

    def add_arguments(self, parser):
        parser.add_argument("--reload", action="store_true", help="Recarga el mismo trabajador entre mensajes cuando se actualiza el código; nunca interrumpe una confirmación en curso.")
        parser.add_argument("--followups", action="store_true", help="Atiende también los informes diarios solicitados y confirmados por cada usuario.")
        parser.add_argument(
            "--once",
            action="store_true",
            help="Procesa como máximo un mensaje y termina.",
        )
        parser.add_argument(
            "--sleep",
            type=float,
            default=0.5,
            help="Segundos de espera cuando la cola está vacía (predeterminado: 0.5).",
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
        requested_sleep = float(options["sleep"])
        if not math.isfinite(requested_sleep):
            raise CommandError("--sleep debe ser un número finito de segundos.")
        sleep_seconds = min(max(requested_sleep, 0.2), 60.0)
        maximum = max(int(options["max_messages"]), 0)
        processed = 0
        watcher = SourceWatcher(settings.BASE_DIR) if options["reload"] and not once else None
        self.stdout.write("Procesador del bot de Telegram iniciado.")
        from mainApp.services.telegram_tts import status as voice_reply_status
        voice_status = voice_reply_status()
        self.stdout.write(f"Voz de salida: {voice_status['mode']} | {voice_status['label']}")
        # Solo este trabajador: no cambiar conexiones de la web ni de comandos
        # externos. Evita reconectar con Aiven varias veces por cada mensaje.
        db_config = connection.settings_dict
        previous_age = db_config.get("CONN_MAX_AGE", 0)
        previous_checks = db_config.get("CONN_HEALTH_CHECKS", False)
        db_config["CONN_MAX_AGE"] = 60
        db_config["CONN_HEALTH_CHECKS"] = True
        transports = ExitStack()
        try:
            transports.enter_context(pooled_http_session())
            last_recovery = None
            last_followups = None
            db_failures = 0
            while True:
                try:
                    close_old_connections()
                    if watcher is not None:
                        state = watcher.poll()
                        if state == "reload":
                            self.stdout.write("Código actualizado: recargando el mismo trabajador entre mensajes. No se reenvían solicitudes terminadas.")
                            self.stdout.flush()
                            close_old_connections()
                            transports.close()
                            try:
                                os.execv(sys.executable, [sys.executable, "-u", *sys.argv])
                            except OSError:
                                transports.enter_context(pooled_http_session())
                                raise
                            return
                        if state == "waiting":
                            time.sleep(sleep_seconds)
                            continue
                    if not is_feature_enabled(TELEGRAM_BOT_FEATURE, fresh=True):
                        if once:
                            self.stdout.write("El bot está desactivado; no se procesó ningún mensaje.")
                            break
                        time.sleep(max(sleep_seconds, 10.0))
                        continue
                    if last_recovery is None or time.monotonic() - last_recovery >= 60:
                        recover_stale_updates()
                        last_recovery = time.monotonic()
                    found = process_next_update()
                    # El chat tiene prioridad. Entre revisiones de cola solo un
                    # informe, evitando que cinco envíos bloqueen al cajero.
                    if not found and options["followups"] and (last_followups is None or time.monotonic() - last_followups >= 10):
                        from mainApp.services.telegram_followups import process_followups
                        process_followups(limit=1)
                        last_followups = time.monotonic()
                    db_failures = 0
                except DatabaseError as exc:
                    self.stderr.write(f"Base de datos no disponible ({type(exc).__name__}); se reintentará sin repetir acciones terminadas.")
                    close_old_connections()
                    if once:
                        raise CommandError("No se pudo consultar la base de datos; no se repetirá ninguna acción en esta ejecución.") from None
                    db_failures = min(db_failures + 1, 4)
                    time.sleep(min(5 * (2 ** (db_failures - 1)), 30.0))
                    continue
                except Exception as exc:
                    self.stderr.write(f"El ciclo del bot se interrumpió ({type(exc).__name__}); se conservará la cola para recuperarla de forma segura.")
                    close_old_connections()
                    if once:
                        raise CommandError("El ciclo no pudo terminar. Revisa la cola antes de repetir una solicitud.") from None
                    time.sleep(10.0)
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
            try:
                transports.close()
            finally:
                db_config["CONN_MAX_AGE"] = previous_age
                db_config["CONN_HEALTH_CHECKS"] = previous_checks
                close_old_connections()
        self.stdout.write(self.style.SUCCESS(f"Mensajes atendidos en esta ejecución: {processed}."))
