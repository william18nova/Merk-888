from django.apps import AppConfig


class LocalPosConfig(AppConfig):
    name = "local_pos"

    def ready(self):
        from django.conf import settings
        if not getattr(settings, "HYBRID_LOCAL_ENABLED", False):
            raise RuntimeError("local_pos no debe instalarse en la aplicación de producción.")
        # La tabla heredada de roles también debe existir en el esquema NUEVO.
        # Solo se cambia metadata en este proceso local; no el modelo compartido.
        from mainApp.models import RolPermiso
        RolPermiso._meta.managed = True
    default_auto_field = "django.db.models.BigAutoField"
