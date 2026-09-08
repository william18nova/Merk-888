from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from pathlib import Path

from mainApp.services import telegram_providers

from mainApp.services.telegram_bot import (
    TelegramBotError,
    _gemini_function_call,
    _groq_function_call,
    _cerebras_function_call,
    _openrouter_function_call,
    transcribe_voice,
    TELEGRAM_FILE_LIMIT,
)


class Command(BaseCommand):
    help = "Diagnostica proveedores de texto/audio sin ejecutar acciones del negocio. --status no consume cuota."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--provider", choices=(*telegram_providers.KEYS, "all"), default="all")
        parser.add_argument("--stage", choices=("text", "voice"), default="text")
        parser.add_argument("--status", action="store_true", help="Solo configuración; no llama a ninguna API.")
        parser.add_argument("--audio-file", help="Archivo de prueba Ogg/Opus para --stage voice; se envía a los proveedores seleccionados.")

    def handle(self, *args, **options):
        if options["status"]:
            try:
                for row in telegram_providers.status():
                    self.stdout.write(f"{row['stage']} · {row['name']}: {row['status']}")
            except TelegramBotError as exc:
                raise CommandError(str(exc)) from None
            return
        if options["stage"] == "voice":
            return self.check_voice(options)
        providers = (
            ("gemini", "GEMINI_API_KEY", _gemini_function_call),
            ("groq", "GROQ_API_KEY", _groq_function_call),
            ("cerebras", "CEREBRAS_API_KEY", _cerebras_function_call),
            ("openrouter", "OPENROUTER_API_KEY", _openrouter_function_call),
        )
        if options["provider"] not in ("all", *(p[0] for p in providers)):
            raise CommandError("Ese proveedor solo sirve para --stage voice.")
        failures = 0
        checked = 0
        for name, key_setting, function in providers:
            if options["provider"] not in ("all", name):
                continue
            reason = telegram_providers.blocked_reason(name)
            if reason or name not in telegram_providers.order("text"):
                self.stdout.write(f"{name.upper()}: {reason or 'Desactivado en el orden de texto'}.")
                failures += options["provider"] != "all"
                continue
            checked += 1
            try:
                tool_name, arguments, text = function("Busca el producto tomate")
                if tool_name != "buscar_producto":
                    raise TelegramBotError("No seleccionó la función esperada de búsqueda.")
            except TelegramBotError as exc:
                self.stdout.write(f"{name.upper()}: ERROR: {exc}")
                failures += 1
            else:
                self.stdout.write(self.style.SUCCESS(f"{name.upper()}: OK, búsqueda interpretada correctamente."))
        if failures:
            raise CommandError(f"{failures} proveedor(es) requieren revisión. No se ejecutó ninguna acción del negocio.")
        if not checked:
            raise CommandError("No hay proveedores de texto listos para comprobar.")
        self.stdout.write("Prueba finalizada sin ejecutar acciones del negocio.")

    def check_voice(self, options):
        if not options["audio_file"]:
            raise CommandError("Indica --audio-file /ruta/prueba.ogg. Usa solo un audio de prueba sin datos sensibles.")
        path = Path(options["audio_file"])
        try:
            if path.stat().st_size > TELEGRAM_FILE_LIMIT:
                raise CommandError("El audio supera 20 MB.")
            audio = path.read_bytes()
        except OSError:
            raise CommandError("No se pudo leer el archivo de audio.") from None
        names = telegram_providers.active("voice")
        if options["provider"] != "all":
            names = [name for name in names if name == options["provider"]]
        if not names:
            raise CommandError("No hay proveedores de audio seleccionados y habilitados; revisa --status.")
        failures = 0
        for name in names:
            # Una llamada por proveedor; nunca interpreta ni ejecuta la transcripción.
            with override_settings(TELEGRAM_VOICE_PROVIDERS=name):
                try:
                    text = transcribe_voice(audio)
                except TelegramBotError as exc:
                    self.stdout.write(f"{name}: {exc}")
                    failures += 1
                else:
                    self.stdout.write(f"{name}: OK ({len(text)} caracteres; transcripción omitida por privacidad).")
        if failures:
            raise CommandError(f"{failures} comprobaciones de voz no finalizaron; revisa el diagnóstico.")
