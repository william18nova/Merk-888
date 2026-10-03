"""Solo PostgreSQL LOCAL desechable: no permite host, usuario ni BD de producción."""
import os
from .test_settings import *  # noqa: F401,F403

if os.environ.get("HYBRID_TEST_POSTGRES") != "yes-local-disposable":
    raise RuntimeError("Este ajuste exige HYBRID_TEST_POSTGRES=yes-local-disposable y un PostgreSQL de pruebas en loopback.")

SECRET_KEY = "local-disposable-postgres-hybrid-tests-only"
# Crear todo el esquema vigente en una sola fase. Si solo mainApp omite
# migraciones, PostgreSQL exige auth_group antes de poder crear sus FK.
MIGRATION_MODULES = {name: None for name in ("mainApp", "auth", "contenttypes", "admin", "sessions")}
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "127.0.0.1",
        "PORT": int(os.environ.get("HYBRID_TEST_POSTGRES_PORT", "55432")),
        "NAME": "nova_hybrid_ci",
        "USER": "nova_hybrid_ci",
        "PASSWORD": os.environ.get("HYBRID_TEST_POSTGRES_PASSWORD", "local-test-only"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 5, "options": "-c lock_timeout=5000 -c statement_timeout=15000"},
        "TEST": {"NAME": "test_nova_hybrid_ci"},
    }
}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "hybrid-tests"}}
