from django.core.management.base import BaseCommand, CommandError

from mainApp.services.telegram_bot import (
    TelegramBotError,
    _configured,
    _gemini_function_call,
    _groq_function_call,
)


class Command(BaseCommand):
    help = "Comprueba Gemini y Groq con texto de prueba, sin consultar ni modificar datos del negocio."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--provider", choices=("gemini", "groq", "all"), default="all")

    def handle(self, *args, **options):
        providers = (
            ("gemini", "GEMINI_API_KEY", _gemini_function_call),
            ("groq", "GROQ_API_KEY", _groq_function_call),
        )
        failures = 0
        for name, key_setting, function in providers:
            if options["provider"] not in ("all", name):
                continue
            if not _configured(key_setting):
                self.stdout.write(f"{name.upper()}: sin clave configurada.")
                failures += 1
                continue
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
        self.stdout.write("Prueba finalizada sin ejecutar acciones del negocio.")
