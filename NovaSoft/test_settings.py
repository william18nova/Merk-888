"""Ajustes aislados para ejecutar pruebas sin tocar la base de producción."""

from .settings import *  # noqa: F401,F403


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# El esquema actual contiene una migración SQL específica de PostgreSQL. Para
# las pruebas unitarias se crea mainApp directamente desde sus modelos vigentes.
MIGRATION_MODULES = {"mainApp": None}

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
