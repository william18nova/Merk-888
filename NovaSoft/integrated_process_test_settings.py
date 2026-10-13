"""Process-test fixture. No import of production settings or Aiven credentials."""
import os
from pathlib import Path

from local_pos.integrated_config import load_config, local_database

if os.environ.get("HYBRID_TEST_POSTGRES") != "yes-local-disposable":
    raise RuntimeError("Only the disposable integrated-process test may use this profile.")

BASE_DIR = Path(__file__).resolve().parents[1]
cloud = load_config(os.environ["NOVA_TEST_CLOUD_CONFIG"], BASE_DIR)
DATABASES = {"default": local_database(cloud)}
SECRET_KEY = "fictional-process-test-no-business-data"
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
AUTH_USER_MODEL = "mainApp.Usuario"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "America/Bogota"
LANGUAGE_CODE = "es-co"
ROOT_URLCONF = "local_pos.test_integrated_requests"
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "dal", "dal_select2", "widget_tweaks", "mainApp"]
MIDDLEWARE = ["django.contrib.sessions.middleware.SessionMiddleware",
              "django.contrib.auth.middleware.AuthenticationMiddleware",
              "mainApp.middleware.PagePermissionMiddleware",
              "django.contrib.messages.middleware.MessageMiddleware"]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages",
        "mainApp.context_processors.pos_agent", "mainApp.context_processors.permissions_nav"]}}]
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "process-test-cloud"}}
STATIC_URL = "/static/"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
