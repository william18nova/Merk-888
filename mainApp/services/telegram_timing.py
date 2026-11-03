"""Tiempos operativos sin mensajes, importes, identificadores de chat ni secretos."""
import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar


logger = logging.getLogger(__name__)
ai_deadline = ContextVar("telegram_ai_deadline", default=None)


def network_timeout(remaining, *, read_limit):
    """Acota conexión + espera de lectura al presupuesto restante.

    requests no garantiza un límite absoluto de reloj (DNS/lecturas parciales).
    Por eso también se comprueba el plazo antes de cada intento.
    """
    connect = min(3.0, remaining / 3)
    return connect, min(read_limit, remaining - connect)


@contextmanager
def measured(stage, *, update_id=None, provider=None):
    started = time.monotonic()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        logger.info("Telegram tiempo etapa=%s update=%s proveedor=%s segundos=%.3f resultado=%s",
                    stage, update_id or "-", provider or "-", time.monotonic() - started, outcome)
