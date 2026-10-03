"""Django completo LOCAL. Independiente de settings.py y de variables de Aiven."""
from pathlib import Path
from local_pos.config import load_config

LOCAL_CONFIG = load_config()
BASE_DIR = Path(__file__).resolve().parents[1]
SECRET_KEY = LOCAL_CONFIG["secret_key"]
DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
ROOT_URLCONF = "local_pos.urls"
AUTH_USER_MODEL = "mainApp.Usuario"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_TZ = True
USE_I18N = True
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
                  "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
                  "dal", "dal_select2", "widget_tweaks", "mainApp", "local_pos"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
              "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
              "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
              "local_pos.middleware.LocalSafetyMiddleware", "mainApp.middleware.PagePermissionMiddleware"]
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "APP_DIRS": True,
              "OPTIONS": {"context_processors": ["django.template.context_processors.debug",
                  "django.template.context_processors.request", "django.contrib.auth.context_processors.auth",
                  "django.contrib.messages.context_processors.messages", "mainApp.context_processors.permissions_nav",
                  "mainApp.context_processors.pos_agent", "local_pos.context.context"]}}]
DATABASES = {"default": {"ENGINE": "django.db.backends.postgresql", "HOST": "127.0.0.1",
    "PORT": LOCAL_CONFIG["port"], "NAME": LOCAL_CONFIG["database"], "USER": LOCAL_CONFIG["user"],
    "PASSWORD": LOCAL_CONFIG["password"], "CONN_MAX_AGE": 0,
    "OPTIONS": {"connect_timeout": 5, "options": "-c lock_timeout=5000 -c statement_timeout=15000"},
    "TEST": {"NAME": "test_nova_full_local_lab"}}}
# Solo laboratorio: esquema de los modelos actuales en una BD NUEVA y ficticia.
# No es aún una estrategia de migración/instalación sobre datos del negocio.
MIGRATION_MODULES = {name: None for name in ("mainApp", "local_pos", "admin", "auth", "contenttypes", "sessions")}
STATIC_URL = "/static/"
STATIC_ROOT = str(Path(LOCAL_CONFIG["data_dir"]) / "staticfiles")
MEDIA_URL = "/media/"
MEDIA_ROOT = str(Path(LOCAL_CONFIG["data_dir"]) / "media")
STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": LOCAL_CONFIG["instance_id"]}}
SESSION_COOKIE_NAME = "nova_local_" + LOCAL_CONFIG["instance_id"].replace("-", "")[:12]
CSRF_COOKIE_NAME = SESSION_COOKIE_NAME + "_csrf"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_SAMESITE = "Strict"
LOGIN_URL = "/"
LOGIN_REDIRECT_URL = "/home/"
LOGOUT_REDIRECT_URL = "/"
EMAIL_BACKEND = "django.core.mail.backends.dummy.EmailBackend"
HYBRID_LOCAL_ENABLED = True
HYBRID_LOCAL_SALES_ENABLED = False  # Solo --sale-demo activa la integración ficticia.
HYBRID_LOCAL_OPERATIONS_ENABLED = False
HYBRID_LOCAL_MODE = LOCAL_CONFIG["mode"]
# Nunca cargar keys de .env ni del entorno de la nube en este runtime.
GEMINI_API_KEY = GROQ_API_KEY = CEREBRAS_API_KEY = OPENROUTER_API_KEY = ""
AZURE_SPEECH_KEY = DEEPGRAM_API_KEY = ASSEMBLYAI_API_KEY = ""
TELEGRAM_BOT_TOKEN = TELEGRAM_WEBHOOK_SECRET = NEQUI_API_KEY = ""
POS_AGENT_TOKEN = INVENTARIO_AGENT_TOKEN = ""
POS_AGENT_URL = "http://127.0.0.1:8787"
INVENTARIO_AGENT_URL = "http://127.0.0.1:8788"
PRICE_SYNC_SOURCE_HOST = PRICE_SYNC_SOURCE_NAME = PRICE_SYNC_SOURCE_USER = PRICE_SYNC_SOURCE_PASSWORD = ""
