"""Laboratorio PTM persistente y aislado; nunca usar para producción."""
from .test_settings import *  # noqa

DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
SECRET_KEY = "solo-laboratorio-ptm-no-produccion"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
    "NAME": BASE_DIR / "build" / "ptm_lab" / "pruebas.sqlite3"}}
DATABASE_ROUTERS = []
PASSWORD_HASHERS = ["django.contrib.auth.hashers.PBKDF2PasswordHasher"]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SESSION_COOKIE_NAME = "novapos_ptm_lab"
CSRF_COOKIE_NAME = "novapos_ptm_lab_csrf"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

