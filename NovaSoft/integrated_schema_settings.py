"""One-time LOCAL schema initializer: never imports Aiven settings."""
import os
from pathlib import Path
from local_pos.runtime import read_json
from local_pos.integrated_config import local_database, DATABASE, FORMAT

BASE_DIR = Path(__file__).resolve().parents[1]
path = Path(os.environ["NOVA_INTEGRATED_CONFIG"])
LOCAL_CONFIG = read_json(path)
if (LOCAL_CONFIG.get("format") != FORMAT or LOCAL_CONFIG.get("phase") != "initializing"
        or LOCAL_CONFIG.get("database") != DATABASE or LOCAL_CONFIG.get("user") != DATABASE
        or Path(LOCAL_CONFIG["data_dir"]).resolve() != path.parent.resolve()):
    raise RuntimeError("El inicializador solo acepta una base privada NUEVA sin activar.")
SECRET_KEY = LOCAL_CONFIG["secret_key"]
DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
AUTH_USER_MODEL = "mainApp.Usuario"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "America/Bogota"
LANGUAGE_CODE = "es-co"
ROOT_URLCONF = "NovaSoft.urls"
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "dal", "dal_select2", "widget_tweaks", "mainApp", "local_pos"]
MIDDLEWARE = ["django.contrib.sessions.middleware.SessionMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
              "django.contrib.messages.middleware.MessageMiddleware"]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
       "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
DATABASES = {"default": local_database(LOCAL_CONFIG)}
MIGRATION_MODULES = {app: None for app in ("mainApp", "local_pos", "auth", "admin", "contenttypes", "sessions")}
HYBRID_LOCAL_ENABLED = True
STATIC_URL = "/static/"
