"""Límites de transporte y errores seguros; no interpreta ni ejecuta acciones."""
import math
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.cookiejar import DefaultCookiePolicy

import requests

logger = logging.getLogger(__name__)
_http_session = ContextVar('telegram_http_session', default=None)


@contextmanager
def pooled_http_session():
    """Reutiliza TLS por destino solo durante la vida del trabajador."""
    with requests.Session() as session:
        # El pool no guarda autenticación ni cookies de otros mensajes. Los
        # encabezados privados solo se entregan en la petición correspondiente.
        session.cookies.set_policy(DefaultCookiePolicy(allowed_domains=()))
        token = _http_session.set(session)
        try:
            yield
        finally:
            _http_session.reset(token)


def http_request(method, url, **kwargs):
    # Session mantiene los timeouts, proxy y TLS de requests sin reintentos
    # automáticos. El trabajador ya distingue fallos seguros de entregas dudosas.
    transport = _http_session.get() or requests
    return getattr(transport, method)(url, allow_redirects=False, **kwargs)


def retry_after(data, headers=None):
    values = []
    parameters = data.get('parameters') if isinstance(data, dict) else None
    if isinstance(parameters, dict):
        values.append(parameters.get('retry_after'))
    for key, value in (headers or {}).items():
        if str(key).lower() == 'retry-after':
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                try:
                    values.append((parsedate_to_datetime(str(value)) - datetime.now(timezone.utc)).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
    waits = []
    for value in values:
        if isinstance(value, bool):
            continue
        try:
            seconds = float(value)
            if math.isfinite(seconds) and seconds > 0:
                waits.append(min(math.ceil(seconds), 86400))
        except (TypeError, ValueError, OverflowError):
            pass
    return max(waits, default=30)


def download_voice_file(token, result, *, limit, label='audio'):
    from .telegram_bot import TelegramBotError, TelegramExternalError, TelegramRateLimit
    overflow = ('El audio supera el límite de 20 MB.' if label == 'audio'
                else f'La foto supera el límite de {limit // (1024 * 1024)} MB.')
    if not isinstance(result, dict):
        raise TelegramBotError('Telegram no devolvió los datos del audio.')
    path, size = result.get('file_path'), result.get('file_size')
    if (not isinstance(path, str) or not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+', path)
            or any(part in {'.', '..'} for part in path.split('/'))):
        raise TelegramBotError('Telegram no devolvió una ruta válida para el audio.')
    if size is not None and (type(size) is not int or size < 0):
        raise TelegramBotError('Telegram no devolvió un tamaño válido para el audio.')
    if size is not None and size > limit:
        raise TelegramBotError(overflow)
    response = None
    deadline = time.monotonic() + 45
    try:
        response = http_request('get', f'https://api.telegram.org/file/bot{token}/{path}',
                                timeout=(3, 20), stream=True)
        if response.status_code == 429:
            raise TelegramRateLimit(retry_after(None, response.headers))
        if response.status_code != 200:
            raise TelegramExternalError('No se pudo descargar el audio desde Telegram.')
        length = response.headers.get('Content-Length')
        if length is not None:
            try:
                if int(length) > limit:
                    raise TelegramBotError(overflow)
            except (TypeError, ValueError):
                pass
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if time.monotonic() >= deadline:
                raise TelegramExternalError('La descarga del audio tardó demasiado. Se intentará de nuevo.')
            if not chunk:
                continue
            total += len(chunk)
            if total > limit:
                raise TelegramBotError(overflow)
            chunks.append(chunk)
        if not total:
            raise TelegramBotError('Telegram entregó un audio vacío. Envía una nota de voz nueva o escribe la solicitud.')
        return b''.join(chunks)
    except requests.RequestException:
        raise TelegramExternalError('Se interrumpió la descarga del audio desde Telegram.') from None
    finally:
        if response is not None:
            try:
                response.close()
            except (OSError, requests.RequestException):
                logger.warning('Telegram cierre_descarga=no_disponible')
