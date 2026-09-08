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

# Las pruebas nunca heredan credenciales de proveedores desde archivos privados.
GEMINI_API_KEY = GROQ_API_KEY = CEREBRAS_API_KEY = OPENROUTER_API_KEY = ""
AZURE_SPEECH_KEY = DEEPGRAM_API_KEY = ASSEMBLYAI_API_KEY = ""
TELEGRAM_TEXT_PROVIDERS = "gemini,groq,cerebras,openrouter"
TELEGRAM_VOICE_PROVIDERS = "groq,azure,deepgram,assemblyai,gemini"
AZURE_SPEECH_FREE_TIER_CONFIRMED = DEEPGRAM_TRIAL_CONFIRMED = ASSEMBLYAI_TRIAL_CONFIRMED = "false"
