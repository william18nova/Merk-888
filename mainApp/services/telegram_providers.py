"""Configuración explícita de proveedores, sin llamadas ni secretos en la UI."""

from django.conf import settings

TEXT_ORDER = "gemini,groq,cerebras,openrouter"
VOICE_ORDER = "groq,azure,deepgram,assemblyai,gemini"
KEYS = {
    "gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY", "openrouter": "OPENROUTER_API_KEY",
    "azure": "AZURE_SPEECH_KEY", "deepgram": "DEEPGRAM_API_KEY",
    "assemblyai": "ASSEMBLYAI_API_KEY",
}
LABELS = {"gemini": "Gemini", "groq": "Groq", "cerebras": "Cerebras",
          "openrouter": "OpenRouter", "azure": "Azure Speech",
          "deepgram": "Deepgram", "assemblyai": "AssemblyAI"}


def value(name, default=""):
    return str(getattr(settings, name, default) or "").strip()


def order(stage):
    default = TEXT_ORDER if stage == "text" else VOICE_ORDER
    raw = value("TELEGRAM_TEXT_PROVIDERS" if stage == "text" else "TELEGRAM_VOICE_PROVIDERS", default)
    names = list(dict.fromkeys(part.strip().lower() for part in raw.split(",") if part.strip()))
    if any(name not in default.split(",") for name in names):
        from .telegram_bot import TelegramConfigurationError
        raise TelegramConfigurationError("Hay un proveedor desconocido en el orden de respaldos de Telegram.")
    return names


def blocked_reason(name):
    if not value(KEYS[name]):
        return "Falta clave"
    confirmations = {
        "azure": "AZURE_SPEECH_FREE_TIER_CONFIRMED",
        "deepgram": "DEEPGRAM_TRIAL_CONFIRMED",
        "assemblyai": "ASSEMBLYAI_TRIAL_CONFIRMED",
    }
    if name in confirmations and value(confirmations[name]).lower() not in {"true", "1", "yes"}:
        return "Pendiente confirmar plan gratuito/créditos"
    if name == "azure" and not value("AZURE_SPEECH_RESOURCE"):
        return "Falta nombre del recurso Azure"
    if name == "openrouter" and not value("OPENROUTER_CHAT_MODEL", "openai/gpt-oss-120b:free").endswith(":free"):
        return "Se requiere un modelo fijo :free"
    return ""


def active(stage):
    return [name for name in order(stage) if not blocked_reason(name)]


def status():
    rows = []
    for stage in ("text", "voice"):
        for name in order(stage):
            reason = blocked_reason(name)
            rows.append({"stage": "Texto" if stage == "text" else "Audio", "name": LABELS[name],
                         "ready": not reason, "status": reason or "Configurado (sin comprobar conexión)"})
    return rows
