"""Keep runserver's original behavior; tolerate only a confirmed cloud outage."""
from django.conf import settings
from django.contrib.staticfiles.management.commands.runserver import Command as OriginalRunserver


class Command(OriginalRunserver):
    def get_handler(self, *args, **options):
        if getattr(settings, "NOVA_INTEGRATED_POS_ENABLED", False):
            # The integrated runtime is bound to loopback. Serve ONLY static
            # finder roots outside the database/auth middleware, even with
            # DEBUG=False. Otherwise an offline gate returns HTML/503 for CSS,
            # JS and images before WhiteNoise can see the request.
            # This doesn't enable debug pages or expose the project directory;
            # Django's static handler still checks paths and honors --nostatic.
            from local_pos.integrated_assets import IntegratedStaticFilesHandler
            handler = super().get_handler(*args, **{**options, "use_static_handler": False})
            return IntegratedStaticFilesHandler(handler) if options["use_static_handler"] else handler
        return super().get_handler(*args, **options)

    def check_migrations(self):
        if not getattr(settings, "NOVA_INTEGRATED_POS_ENABLED", False):
            return super().check_migrations()
        from local_pos.cloud_health import monitor
        from local_pos.connectivity import Health
        health = monitor.snapshot()
        if health == Health.DISCONNECTED:
            self.stdout.write("Nube sin conexión: se verificará la autorización de la copia local en cada solicitud.")
            return
        if health != Health.AVAILABLE:
            # Do not open a separate unbounded DB attempt after a bounded probe.
            self.stdout.write("La nube aún no está validada. Las operaciones quedan bloqueadas hasta verificar conexión y esquema.")
            return
        return super().check_migrations()
