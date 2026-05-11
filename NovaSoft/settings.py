# settings.py – perfil simple para runserver local
from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = True

SECRET_KEY = "django-insecure-k!0q10!2q+_i^ni9rz#a+8p!%n+um*7k&3+$=in3dom^6uy5as"

ALLOWED_HOSTS = ["*", "localhost", "127.0.0.1", "[::1]"]

CSRF_TRUSTED_ORIGINS = [
    "https://williamnova18.pythonanywhere.com",
    "https://www.williamnova18.pythonanywhere.com",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "mainApp",
    "dal",
    "dal_select2",
    "csp",  # ✅
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "mainApp.middleware.PagePermissionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "csp.middleware.CSPMiddleware",  # ✅
]

ROOT_URLCONF = "NovaSoft.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates", BASE_DIR / "mainApp" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "mainApp.context_processors.pos_agent",
                "mainApp.context_processors.permissions_nav",
            ],
        },
    },
]

WSGI_APPLICATION = "NovaSoft.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "defaultdb",
        "USER": "avnadmin",
        "PASSWORD": "AVNS_R_lwd-6ff6NHhd_OkSn",
        "HOST": "merk-888-merk888.c.aivencloud.com",
        "PORT": "24553",
        "OPTIONS": {"sslmode": "require"},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "mainApp" / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

AUTH_USER_MODEL = "mainApp.Usuario"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_NAME = "sessionid"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_SAVE_EVERY_REQUEST = True

APPEND_SLASH = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"django": {"handlers": ["console"], "level": "INFO", "propagate": True}},
}

# ========= ✅ NUEVO FORMATO CSP (django-csp >= 4.0) =========
# Quita cualquier CSP_* viejo. Usa este diccionario.
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ("'self'",),
        "script-src": ("'self'", "https:", "'unsafe-inline'"),
        "style-src":  ("'self'", "https:", "'unsafe-inline'"),
        "img-src":    (
            "'self'",
            "https:",
            "data:",
            "blob:",
            "http://127.0.0.1:8788",
            "http://localhost:8788",
        ),
        "font-src":   ("'self'", "https:", "data:"),
        "connect-src": (
            "'self'",
            "https:",
            "http://127.0.0.1:8787",
            "http://localhost:8787",
            "http://127.0.0.1:8788",
            "http://localhost:8788",
        ),
    }
}
# Si prefieres solo ver violaciones sin bloquear, usa “REPORT ONLY”:
# CONTENT_SECURITY_POLICY_REPORT_ONLY = CONTENT_SECURITY_POLICY

# ========= Variables del agente POS (inyectadas al front) =========
POS_AGENT_URL = os.getenv("POS_AGENT_URL", "http://127.0.0.1:8787")
POS_AGENT_TOKEN = os.getenv(
    "POS_AGENT_TOKEN",
    "BmFclqQdWkKjArLIYvakHG426BuLDUtJA0zVG5DJOgjZTWSEVa_i0hxiyXskSHUi"
)

# Token privado que MacroDroid debe enviar en X-Macrodroid-Token.
MACRODROID_NEQUI_TOKEN = os.getenv("MACRODROID_NEQUI_TOKEN", "")

# ========= Variables del agente local de inventario por fotos =========
INVENTARIO_AGENT_URL = os.getenv("INVENTARIO_AGENT_URL", "http://127.0.0.1:8788")
INVENTARIO_AGENT_TOKEN = os.getenv(
    "INVENTARIO_AGENT_TOKEN",
    POS_AGENT_TOKEN,
)
INVENTARIO_FOTOS_ALLOW_SERVER_PROCESS = os.getenv("INVENTARIO_FOTOS_ALLOW_SERVER_PROCESS", "0") == "1"
INVENTARIO_FOTOS_SCRIPT = os.getenv("INVENTARIO_FOTOS_SCRIPT", str(BASE_DIR / "gemini_selenium_cli.py"))
INVENTARIO_FOTOS_TIMEOUT = int(os.getenv("INVENTARIO_FOTOS_TIMEOUT", "900"))
