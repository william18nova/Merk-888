"""Lectura opcional de respuestas: nunca interpreta ni vuelve a ejecutar acciones.

Texto y botones se entregan primero. La voz usa una cola pequeña en memoria,
sin archivos, sin transacciones de negocio y sin reintentar entregas inciertas.
"""
import base64
import binascii
from collections import deque
from dataclasses import dataclass, field
import hashlib
import io
import json
import logging
import math
import re
import shutil
import subprocess
from threading import Lock, Thread
import time
import wave

import requests
from django.db import close_old_connections

from . import telegram_providers as providers
from .telegram_timing import measured
from .telegram_transport import http_request

logger = logging.getLogger(__name__)
MODELS = frozenset({'gemini-3.8-flash-lite-tts', 'gemini-3.8-flash-tts'})
MAX_TEXT = 1200
MAX_SECONDS = 120
MAX_AUDIO = 2 * 1024 * 1024
MAX_JSON = 8 * 1024 * 1024
JOB_TTL = 90
_cooldown = {}
LONG_REPLY_NOTICE = ('Te envié la respuesta completa por escrito. Revisa allí los valores y el estado de tu solicitud. '
                     'Utiliza los botones del mensaje cuando necesites confirmar un cambio.')


class SpeechUnavailable(RuntimeError):
    """Solo categorías conocidas: no conserva respuestas HTTP ni secretos."""

    def __init__(self, kind, *, status=None):
        self.kind = kind if kind in {'configuration', 'timeout', 'size', 'format', 'cooldown', 'provider', 'unavailable'} else 'unavailable'
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__('No se pudo generar la lectura de voz; la respuesta escrita se conserva.')


@dataclass(frozen=True)
class BotVoice:
    data: bytes = field(repr=False)
    duration: int
    name: str = 'novapos-respuesta.ogg'
    mime: str = 'audio/ogg'


@dataclass(frozen=True)
class AudioReplyJob:
    update_id: int
    chat_id: int = field(repr=False)
    user_id: int = field(repr=False)
    message_id: int
    text: str = field(repr=False)
    created_at: float


def mode():
    value = providers.value('TELEGRAM_AUDIO_REPLIES', 'off').lower()
    return value if value in {'off', 'voice', 'always'} else 'off'


def status():
    selected = mode()
    reason = ''
    model = providers.value('GEMINI_TTS_MODEL', 'gemini-3.8-flash-lite-tts')
    voice = providers.value('GEMINI_TTS_VOICE', 'Kore')
    if selected == 'off':
        reason = 'Desactivadas'
    elif not providers.value('GEMINI_API_KEY'):
        reason = 'Falta configurar Gemini'
    elif model not in MODELS:
        reason = 'Modelo de voz no admitido'
    elif not re.fullmatch(r'[A-Za-z]{3,30}', voice):
        reason = 'Voz no válida'
    elif not shutil.which(providers.value('TELEGRAM_FFMPEG_BIN', 'ffmpeg')):
        reason = 'Falta FFmpeg para enviar notas de voz'
    return {'ready': not reason, 'mode': selected,
            'label': reason or ('Audio y texto' if selected == 'always' else 'Audio al recibir una nota de voz'),
            'provider': 'Gemini', 'model': model, 'voice': voice}


def spoken_text(reply):
    # Los códigos no salen hacia un servicio de síntesis ni se leen en público.
    if (getattr(reply, 'copyable_code', None) is not None
            or getattr(reply, 'intent', '') in {'clave_merk2_generada', 'vincular', 'sin_vinculo'}
            or getattr(reply, 'history_text', None) is not None):
        return ''
    text = str(getattr(reply, 'text', '') or '').strip()
    if not text:
        return ''
    # No resumir con otra IA ni cortar un importe o condición a mitad de frase.
    if len(text) > MAX_TEXT:
        text = LONG_REPLY_NOTICE
    else:
        text = re.sub(r'https?://[^\s<>]+', 'enlace en el mensaje escrito', text)
        text = re.sub(r'(?m)^\s*[•*]\s+', '', text)
        text = text.replace('`', '').replace('**', '')
        # Gemini 3.8 interpreta etiquetas vocales entre ángulos: neutralizarlas.
        text = text.replace('<', ' menor que ').replace('>', ' mayor que ')
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
        if len(text) > MAX_TEXT:
            text = LONG_REPLY_NOTICE
    if getattr(reply, 'proposal_id', None):
        text += (' Para autorizar esta solicitud, usa el botón de confirmación del mensaje escrito. '
                 'Este audio no confirma la operación.')
    return text


def _read_json(response, deadline):
    body, size = [], 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        if time.monotonic() >= deadline:
            raise SpeechUnavailable('timeout')
        size += len(chunk)
        if size > MAX_JSON:
            raise SpeechUnavailable('size')
        body.append(chunk)
    try:
        return json.loads(b''.join(body))
    except (ValueError, UnicodeError):
        raise SpeechUnavailable('format') from None


def _pcm(data):
    try:
        candidates = data['candidates']
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise ValueError
        if candidates[0].get('finishReason') != 'STOP':
            raise ValueError
        parts = candidates[0]['content']['parts']
        audio = [part['inlineData'] for part in parts if isinstance(part, dict) and 'inlineData' in part]
        if len(audio) != 1:
            raise ValueError
        inline = audio[0]
        encoded, mime = inline['data'], inline['mimeType'].lower()
        if not isinstance(encoded, str) or len(encoded) > MAX_JSON:
            raise ValueError
        raw = base64.b64decode(encoded, validate=True)
        media, *parameters = [part.strip() for part in mime.split(';')]
        parameters = dict(part.split('=', 1) for part in parameters)
        if media in {'audio/wav', 'audio/x-wav', 'audio/wave'}:
            with wave.open(io.BytesIO(raw), 'rb') as source:
                if (source.getnchannels() != 1 or source.getsampwidth() != 2
                        or source.getframerate() != 24000 or source.getcomptype() != 'NONE'
                        or not 0 < source.getnframes() <= MAX_SECONDS * 24000):
                    raise ValueError
                pcm = source.readframes(source.getnframes())
                if len(pcm) != source.getnframes() * 2:
                    raise ValueError
        elif (media == 'audio/l16' and parameters.get('rate') == '24000'
                and parameters.get('codec', 'pcm') == 'pcm' and parameters.get('channels', '1') == '1'):
            pcm = raw
        else:
            raise ValueError
        if not pcm or len(pcm) % 2 or len(pcm) > MAX_SECONDS * 24000 * 2:
            raise ValueError
        return pcm
    except (KeyError, TypeError, ValueError, AttributeError, binascii.Error, wave.Error, EOFError):
        raise SpeechUnavailable('format') from None


def synthesize(text):
    config = status()
    if not config['ready'] or not isinstance(text, str) or not 1 <= len(text) <= MAX_TEXT + 200:
        raise SpeechUnavailable('configuration')
    key = providers.value('GEMINI_API_KEY')
    signature = hashlib.sha256((key + config['model']).encode()).hexdigest()
    if time.monotonic() < _cooldown.get(signature, 0):
        raise SpeechUnavailable('cooldown')
    response = None
    deadline = time.monotonic() + 22
    try:
        with measured('sintetizar_voz', provider='Gemini'):
            response = http_request('post',
                f"https://generativelanguage.googleapis.com/v1beta/models/{config['model']}:generateContent",
                headers={'x-goog-api-key': key, 'Content-Type': 'application/json'},
                json={'contents': [{'role': 'user', 'parts': [{'text': text,
                    'speech_metadata': {'style': 'Lectura clara y fiel en español de Colombia. No añadir, resumir ni cambiar palabras o cifras.'}}]}],
                    'generationConfig': {'responseModalities': ['AUDIO'],
                        'speechConfig': {'voiceConfig': {'voice': config['voice']}}}},
                timeout=(3, 15), stream=True)
            if response.status_code != 200:
                _cooldown.clear()
                _cooldown[signature] = time.monotonic() + (300 if response.status_code in {401, 403, 429} else 30)
                raise SpeechUnavailable('provider', status=response.status_code)
            pcm = _pcm(_read_json(response, deadline))
        if time.monotonic() >= deadline:
            raise SpeechUnavailable('timeout')
        result = subprocess.run([
            providers.value('TELEGRAM_FFMPEG_BIN', 'ffmpeg'), '-nostdin', '-hide_banner', '-loglevel', 'error',
            '-f', 's16le', '-ar', '24000', '-ac', '1', '-i', 'pipe:0',
            '-map_metadata', '-1', '-c:a', 'libopus', '-b:a', '24k', '-f', 'ogg', 'pipe:1',
        ], input=pcm, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=min(8, max(1, deadline - time.monotonic())), check=True)
        if (not result.stdout.startswith(b'OggS') or b'OpusHead' not in result.stdout[:128]
                or not 1 <= len(result.stdout) <= MAX_AUDIO):
            raise SpeechUnavailable('format')
        return BotVoice(data=result.stdout, duration=max(1, math.ceil(len(pcm) / (24000 * 2))))
    except (requests.RequestException, OSError, subprocess.SubprocessError):
        raise SpeechUnavailable('unavailable') from None
    finally:
        if response is not None:
            try:
                response.close()
            except (OSError, requests.RequestException):
                logger.warning('Telegram voz_salida cierre_conexion=no_disponible')


def _still_authorized(job):
    from mainApp.models import TelegramActualizacion, TelegramUsuario
    from .feature_flags import TELEGRAM_BOT_FEATURE, is_feature_enabled
    return (is_feature_enabled(TELEGRAM_BOT_FEATURE, fresh=True)
        and TelegramUsuario.objects.filter(telegram_user_id=job.user_id, telegram_chat_id=job.chat_id,
            activo=True, usuario__is_active=True).exists()
        and TelegramActualizacion.objects.filter(pk=job.update_id, estado='PROCESADO',
            telegram_user_id=job.user_id, telegram_chat_id=job.chat_id, chat_type='private').exists())


def _deliver_job(job):
    from .telegram_bot import TelegramApiClient
    try:
        close_old_connections()
        if (time.monotonic() - job.created_at >= JOB_TTL or not status()['ready']
                or not _still_authorized(job)):
            return
        audio = synthesize(job.text)
        if (time.monotonic() - job.created_at >= JOB_TTL or not status()['ready']
                or not _still_authorized(job)):
            return
        wait = max(0, job.created_at + 1.1 - time.monotonic())
        if wait:
            time.sleep(wait)
        TelegramApiClient().send_voice(job.chat_id, audio, reply_to_message_id=job.message_id)
    except Exception as exc:
        # Un fallo de voz no cambia el estado del pago ni de la respuesta escrita.
        logger.warning('Telegram voz_salida=no_disponible update=%s clase=%s motivo=%s http=%s',
            job.update_id, type(exc).__name__, exc.kind if isinstance(exc, SpeechUnavailable) else '-',
            exc.status if isinstance(exc, SpeechUnavailable) else '-')
    finally:
        close_old_connections()


class AudioReplyDispatcher:
    """Un solo hilo opcional; hasta tres textos en espera, sin archivos/BD de voz."""

    def __init__(self, deliver=_deliver_job):
        self.deliver = deliver
        self.pending, self.seen = deque(), deque(maxlen=128)
        self.lock, self.thread = Lock(), None

    def submit(self, job):
        with self.lock:
            if job.update_id in self.seen or len(self.pending) >= 3:
                return False
            self.pending.append(job)
            self.seen.append(job.update_id)
            if self.thread is None:
                self.thread = Thread(target=self._drain, name='telegram-voice-replies', daemon=True)
                try:
                    self.thread.start()
                except RuntimeError:
                    self.thread = None
                    self.pending.pop()
                    self.seen.remove(job.update_id)
                    return False
        return True

    def _drain(self):
        while True:
            with self.lock:
                if not self.pending:
                    self.thread = None
                    return
                job = self.pending.popleft()
            try:
                if time.monotonic() - job.created_at < JOB_TTL:
                    self.deliver(job)
            except Exception as exc:
                logger.warning('Telegram voz_salida cola_error=%s', type(exc).__name__)
            finally:
                job = None


_dispatcher = AudioReplyDispatcher()


def enqueue_reply(update, reply, delivery):
    selected = mode()
    if (selected == 'off' or update.chat_type != 'private' or update.estado != 'PROCESADO'
            or selected == 'voice' and update.tipo != 'VOZ'):
        return False
    text = spoken_text(reply)
    message_id = delivery.get('message_id') if isinstance(delivery, dict) else None
    if (not text or not status()['ready'] or type(message_id) is not int or message_id <= 0
            or type(update.pk) is not int or type(update.telegram_chat_id) is not int
            or type(update.telegram_user_id) is not int or update.telegram_chat_id <= 0 or update.telegram_user_id <= 0):
        return False
    return _dispatcher.submit(AudioReplyJob(update_id=update.pk, chat_id=update.telegram_chat_id,
        user_id=update.telegram_user_id, message_id=message_id, text=text, created_at=time.monotonic()))
