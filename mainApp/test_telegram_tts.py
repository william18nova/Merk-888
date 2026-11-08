"""Voz de salida con datos ficticios, sin Telegram/IA/red ni pagos reales."""
import base64
from dataclasses import replace
import io
import json
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch
import wave
import time

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from django.core.management import call_command
from django.utils import timezone

from .models import (ConfiguracionFuncionalidad, Egreso, Rol, TelegramActualizacion,
                     TelegramUsuario, Usuario)
from .services import telegram_bot as bot, telegram_tts as tts
from .services.feature_flags import TELEGRAM_BOT_FEATURE, clear_feature_cache

OGG = b'OggS' + b'\0' * 16 + b'OpusHead' + b'\x01' * 20
PCM = b'\0\0' * 24000


def wav(pcm=PCM):
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(pcm)
    return output.getvalue()


def speech_data(audio=None, mime='audio/wav', finish='STOP'):
    return {'candidates': [{'finishReason': finish, 'content': {'parts': [
        {'inlineData': {'mimeType': mime, 'data': base64.b64encode(wav() if audio is None else audio).decode()}}
    ]}}]}


def response(data=None, status=200, chunks=None):
    payload = json.dumps(speech_data() if data is None else data).encode()
    return NS(status_code=status, close=MagicMock(),
              iter_content=MagicMock(return_value=iter([payload] if chunks is None else chunks)))


def job(**changes):
    values = dict(update_id=1, chat_id=11, user_id=11, message_id=123,
                  text='Respuesta de prueba por 70.000 pesos.', created_at=100.0)
    values.update(changes)
    return tts.AudioReplyJob(**values)


class SpokenTextTests(SimpleTestCase):
    def test_codes_and_redacted_replies_are_not_sent_to_synthesis(self):
        cases = [bot.BotReply('Clave creada.', 'clave_merk2_generada', copyable_code='00001234'),
                 bot.BotReply('Ya está.', history_text='Resumen seguro'),
                 bot.BotReply('Usa /vincular ABC123', 'vincular'),
                 bot.BotReply('Vincula tu cuenta.', 'sin_vinculo')]
        for reply in cases:
            with self.subTest(intent=reply.intent):
                self.assertEqual(tts.spoken_text(reply), '')

    def test_short_reply_keeps_amounts_dates_and_confirmation_requirement(self):
        reply = bot.BotReply('Nequi: $ 85.900. Fecha 08/11/2026. Aún no se registró.',
                             'preparar_pago', proposal_id='demo')
        text = tts.spoken_text(reply)
        self.assertTrue(text.startswith(reply.text))
        self.assertIn('botón de confirmación', text)
        self.assertIn('Este audio no confirma la operación', text)

    def test_long_reply_is_not_a_silent_truncated_financial_summary(self):
        reply = bot.BotReply('Dato de prueba. ' * 100 + 'Saldo final: $ 123.456.')
        text = tts.spoken_text(reply)
        self.assertIn('respuesta completa por escrito', text)
        self.assertNotIn('Dato de prueba', text)
        self.assertNotIn('123.456', text)
        self.assertLess(len(text), tts.MAX_TEXT)

    def test_urls_and_speech_tags_do_not_become_extra_instructions(self):
        text = tts.spoken_text(bot.BotReply('• **Pago** `85.900` <laugh> https://example.invalid/privado'))
        self.assertIn('85.900', text)
        self.assertNotIn('https://', text)
        self.assertNotIn('<laugh>', text)
        self.assertNotIn('**', text)

    def test_audio_bytes_and_text_are_not_in_dataclass_repr(self):
        self.assertNotIn('OggS', repr(tts.BotVoice(OGG, 1)))
        self.assertNotIn('70.000', repr(job()))


@override_settings(TELEGRAM_AUDIO_REPLIES='voice', GEMINI_API_KEY='TEST_ONLY_TTS_KEY',
                   GEMINI_TTS_MODEL='gemini-3.8-flash-lite-tts', GEMINI_TTS_VOICE='Kore')
class SpeechSynthesisTests(SimpleTestCase):
    def setUp(self):
        tts._cooldown.clear()
        self.addCleanup(tts._cooldown.clear)
        which = patch.object(tts.shutil, 'which', return_value='ffmpeg-test-only')
        which.start()
        self.addCleanup(which.stop)

    def test_disabled_missing_key_unapproved_model_or_invalid_voice_do_not_call_network(self):
        for values in ({'TELEGRAM_AUDIO_REPLIES': 'off'}, {'GEMINI_API_KEY': ''},
                       {'GEMINI_TTS_MODEL': 'https://evil.invalid/model'}, {'GEMINI_TTS_VOICE': 'Kore/../../'}):
            with self.subTest(values=values), override_settings(**values), patch.object(tts, 'http_request') as http:
                self.assertFalse(tts.status()['ready'])
                with self.assertRaises(tts.SpeechUnavailable):
                    tts.synthesize('Prueba sin datos reales.')
                http.assert_not_called()

    def test_ffmpeg_is_checked_before_sending_private_text(self):
        with patch.object(tts.shutil, 'which', return_value=None), patch.object(tts, 'http_request') as http:
            self.assertIn('FFmpeg', tts.status()['label'])
            with self.assertRaises(tts.SpeechUnavailable):
                tts.synthesize('PRUEBA PRIVADA')
        http.assert_not_called()

    def test_wav_is_read_as_pcm_and_converted_to_a_telegram_voice_note(self):
        reply = response()
        with patch.object(tts.requests, 'post', return_value=reply) as post, \
                patch.object(tts.subprocess, 'run', return_value=NS(stdout=OGG)) as convert:
            audio = tts.synthesize('Pago de prueba: $ 70.000.')
        self.assertEqual((audio.data, audio.duration, audio.mime), (OGG, 1, 'audio/ogg'))
        request = post.call_args.kwargs
        self.assertFalse(request['allow_redirects'])
        self.assertTrue(request['stream'])
        self.assertEqual(request['timeout'], (3, 15))
        self.assertEqual(request['headers']['x-goog-api-key'], 'TEST_ONLY_TTS_KEY')
        self.assertNotIn('TEST_ONLY_TTS_KEY', post.call_args.args[0])
        self.assertEqual(request['json']['generationConfig']['speechConfig']['voiceConfig'], {'voice': 'Kore'})
        self.assertNotIn('tools', request['json'])
        self.assertEqual(convert.call_args.kwargs['input'], PCM)
        self.assertNotIn('-t', convert.call_args.args[0])
        self.assertIn('pipe:1', convert.call_args.args[0])
        reply.close.assert_called_once()

    def test_documented_raw_pcm_output_is_supported(self):
        for mime in ('audio/L16;codec=pcm;rate=24000', 'audio/L16;rate=24000'):
            with self.subTest(mime=mime):
                self.assertEqual(tts._pcm(speech_data(PCM, mime)), PCM)

    def test_rejects_incomplete_invalid_or_oversize_audio_instead_of_reading_part_of_reply(self):
        cases = [speech_data(finish='MAX_TOKENS'), speech_data(b'bad', 'audio/mp3'),
                 speech_data(b'1', 'audio/L16;rate=24000'), speech_data(b'', 'audio/L16;rate=24000'),
                 speech_data(wav()[:-2]), {'candidates': []}]
        invalid_base64 = speech_data()
        invalid_base64['candidates'][0]['content']['parts'][0]['inlineData']['data'] = 'not base64!'
        cases.append(invalid_base64)
        for data in cases:
            with self.subTest(data=str(data)[:50]), self.assertRaises(tts.SpeechUnavailable):
                tts._pcm(data)
        with patch.object(tts, 'MAX_SECONDS', 0), self.assertRaises(tts.SpeechUnavailable):
            tts._pcm(speech_data())

    def test_non_success_response_sets_cooldown_without_retries_or_error_body_logging(self):
        reply = response(status=429)
        with patch.object(tts.requests, 'post', return_value=reply) as post, \
                patch.object(tts.subprocess, 'run') as convert:
            for _ in range(2):
                with self.assertRaises(tts.SpeechUnavailable) as failure:
                    tts.synthesize('PRUEBA PRIVADA')
                self.assertIn(failure.exception.kind, {'provider', 'cooldown'})
                if failure.exception.kind == 'provider':
                    self.assertEqual(failure.exception.status, 429)
        post.assert_called_once()
        convert.assert_not_called()
        reply.iter_content.assert_not_called()
        reply.close.assert_called_once()

    def test_response_is_bounded_before_base64_decode(self):
        reply = response(chunks=[b'x' * 10])
        with patch.object(tts, 'MAX_JSON', 5), patch.object(tts.requests, 'post', return_value=reply), \
                patch.object(tts.subprocess, 'run') as convert, self.assertRaises(tts.SpeechUnavailable):
            tts.synthesize('Prueba')
        convert.assert_not_called()
        reply.close.assert_called_once()

    def test_network_converter_and_malformed_results_fail_without_secret_diagnostics(self):
        with patch.object(tts.requests, 'post', side_effect=requests.ReadTimeout('PRIVATE TEST_ONLY_TTS_KEY')):
            with self.assertRaises(tts.SpeechUnavailable) as failure:
                tts.synthesize('Prueba')
        self.assertNotIn('PRIVATE', str(failure.exception))
        with patch.object(tts.requests, 'post', return_value=response()), \
                patch.object(tts.subprocess, 'run', return_value=NS(stdout=b'not ogg')):
            with self.assertRaises(tts.SpeechUnavailable):
                tts.synthesize('Prueba')


@override_settings(TELEGRAM_BOT_TOKEN='TEST_ONLY_BOT_TOKEN')
class VoiceTransportTests(SimpleTestCase):
    def test_upload_is_bound_to_written_reply_and_has_no_action_buttons(self):
        client = bot.TelegramApiClient()
        with patch.object(client, '_post', return_value={'message_id': 22}) as post:
            self.assertEqual(client.send_voice(11, tts.BotVoice(OGG, 1), reply_to_message_id=123), {'message_id': 22})
        method = post.call_args.args[0]
        payload = post.call_args.kwargs['payload']
        self.assertEqual(method, 'sendVoice')
        self.assertEqual(json.loads(payload['reply_parameters']), {'message_id': 123})
        self.assertTrue(payload['disable_notification'])
        self.assertTrue(payload['protect_content'])
        self.assertNotIn('reply_markup', payload)
        self.assertEqual(post.call_args.kwargs['files']['voice'], ('novapos-respuesta.ogg', OGG, 'audio/ogg'))

    def test_invalid_files_durations_or_message_ids_cannot_be_sent(self):
        good = tts.BotVoice(OGG, 1)
        cases = [(replace(good, data='not bytes'), 123), (replace(good, data=b'html'), 123),
                 (replace(good, duration=121), 123), (replace(good, name='private.txt'), 123),
                 (good, True), (good, 0)]
        client = bot.TelegramApiClient()
        with patch.object(client, '_post') as post:
            for audio, message_id in cases:
                with self.subTest(audio=repr(audio), message_id=message_id), self.assertRaises(bot.TelegramBotError):
                    client.send_voice(11, audio, reply_to_message_id=message_id)
        post.assert_not_called()

    def test_uncertain_upload_is_not_automatically_retried(self):
        with patch.object(bot.requests, 'post', side_effect=requests.ReadTimeout('PRIVATE TOKEN')) as post:
            with self.assertRaises(bot.TelegramDeliveryUncertain) as failure:
                bot.TelegramApiClient().send_voice(11, tts.BotVoice(OGG, 1), reply_to_message_id=123)
        post.assert_called_once()
        self.assertNotIn('PRIVATE', str(failure.exception))


class AudioDispatcherTests(SimpleTestCase):
    def test_real_background_worker_accepts_another_job_while_synthesis_is_busy(self):
        started, release = Event(), Event()
        received = []

        def deliver(item):
            started.set()
            if not release.wait(2):
                raise RuntimeError('Test timeout')
            received.append(item.update_id)

        dispatcher = tts.AudioReplyDispatcher(deliver)
        worker = None
        try:
            self.assertTrue(dispatcher.submit(job(created_at=time.monotonic())))
            worker = dispatcher.thread
            self.assertTrue(started.wait(2))
            self.assertTrue(dispatcher.submit(job(update_id=2, created_at=time.monotonic())))
            self.assertEqual(received, [])
        finally:
            release.set()
            if worker is not None:
                worker.join(3)
        self.assertEqual(received, [1,2])

    def test_submit_never_runs_synthesis_inline_and_bounds_and_deduplicates_queue(self):
        deliver = MagicMock()
        dispatcher = tts.AudioReplyDispatcher(deliver)
        with patch.object(tts, 'Thread') as thread, patch.object(tts.time, 'monotonic', return_value=100):
            self.assertTrue(dispatcher.submit(job()))
            self.assertFalse(dispatcher.submit(job()))
            self.assertTrue(dispatcher.submit(job(update_id=2)))
            self.assertTrue(dispatcher.submit(job(update_id=3)))
            self.assertFalse(dispatcher.submit(job(update_id=4)))
            deliver.assert_not_called()
            thread.assert_called_once()
            self.assertTrue(thread.call_args.kwargs['daemon'])
            dispatcher._drain()
        self.assertEqual([call.args[0].update_id for call in deliver.call_args_list], [1,2,3])
        self.assertIsNone(dispatcher.thread)

    def test_expired_jobs_are_discarded_without_network_and_errors_do_not_kill_queue(self):
        deliver = MagicMock(side_effect=[RuntimeError('PRIVATE TEXT'), None])
        dispatcher = tts.AudioReplyDispatcher(deliver)
        dispatcher.pending.extend([job(created_at=0), job(update_id=2), job(update_id=3)])
        with patch.object(tts.time, 'monotonic', return_value=100), self.assertLogs(tts.logger, level='WARNING') as logs:
            dispatcher._drain()
        self.assertEqual([call.args[0].update_id for call in deliver.call_args_list], [2,3])
        self.assertNotIn('PRIVATE', str(logs.output))

    def test_enqueue_failure_does_not_leave_a_job_without_worker(self):
        dispatcher = tts.AudioReplyDispatcher(MagicMock())
        with patch.object(tts, 'Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('thread unavailable')
            self.assertFalse(dispatcher.submit(job()))
        self.assertEqual(list(dispatcher.pending), [])
        self.assertEqual(list(dispatcher.seen), [])

    @override_settings(TELEGRAM_AUDIO_REPLIES='voice')
    def test_only_successful_private_audio_requests_get_an_optional_voice(self):
        update = NS(pk=1, telegram_chat_id=11, telegram_user_id=11, estado='PROCESADO', tipo='VOZ', chat_type='private')
        with patch.object(tts, 'status', return_value={'ready': True}), patch.object(tts._dispatcher, 'submit', return_value=True) as submit:
            self.assertTrue(tts.enqueue_reply(update, bot.BotReply('Prueba'), {'message_id': 123}))
            for changes in ({'tipo':'TEXTO'}, {'estado':'ERROR'}, {'chat_type':'group'}):
                changed = NS(**dict(vars(update), **changes))
                self.assertFalse(tts.enqueue_reply(changed, bot.BotReply('Prueba'), {'message_id':123}))
        submit.assert_called_once()

    @override_settings(TELEGRAM_AUDIO_REPLIES='always')
    def test_always_mode_includes_text_but_requires_a_real_delivered_message_id(self):
        update = NS(pk=1, telegram_chat_id=11, telegram_user_id=11, estado='PROCESADO', tipo='TEXTO', chat_type='private')
        with patch.object(tts, 'status', return_value={'ready': True}), patch.object(tts._dispatcher, 'submit', return_value=True) as submit:
            for delivery in (None, {}, {'message_id': True}, {'message_id':0}):
                self.assertFalse(tts.enqueue_reply(update, bot.BotReply('Prueba'), delivery))
            self.assertTrue(tts.enqueue_reply(update, bot.BotReply('Prueba'), {'message_id':123}))
        submit.assert_called_once()

    def test_delivery_rechecks_authorization_and_does_not_replay_business_actions(self):
        with patch.object(tts.time, 'monotonic', return_value=100), \
                patch.object(tts, 'status', return_value={'ready':True}), \
                patch.object(tts, '_still_authorized', side_effect=[True,False]), \
                patch.object(tts, 'close_old_connections'), \
                patch.object(tts, 'synthesize', return_value=tts.BotVoice(OGG,1)) as synthesize, \
                patch.object(bot, 'TelegramApiClient') as client:
            tts._deliver_job(job())
        synthesize.assert_called_once()
        client.assert_not_called()

    def test_uncertain_optional_audio_is_only_logged_and_never_resent(self):
        with patch.object(tts.time, 'monotonic', return_value=100), \
                patch.object(tts, 'status', return_value={'ready':True}), \
                patch.object(tts, '_still_authorized', return_value=True), patch.object(tts, 'close_old_connections'), \
                patch.object(tts, 'synthesize', return_value=tts.BotVoice(OGG,1)), \
                patch.object(bot, 'TelegramApiClient') as client, self.assertLogs(tts.logger, level='WARNING') as logs:
            client.return_value.send_voice.side_effect = bot.TelegramDeliveryUncertain('PRIVATE TOKEN')
            tts._deliver_job(job())
        client.return_value.send_voice.assert_called_once()
        self.assertNotIn('PRIVATE', str(logs.output))


class AudioReplyIntegrationTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user('Prueba de voz', rolid=Rol.objects.create(nombre='Web Master'))
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=11, telegram_chat_id=11)
        ConfiguracionFuncionalidad.objects.update_or_create(clave=TELEGRAM_BOT_FEATURE, defaults={'habilitada':True})
        clear_feature_cache(TELEGRAM_BOT_FEATURE)
        self.addCleanup(clear_feature_cache, TELEGRAM_BOT_FEATURE)
        self.update = TelegramActualizacion.objects.create(update_id=1, telegram_user_id=11,
            telegram_chat_id=11, chat_type='private', tipo='VOZ', transcripcion='Consulta de prueba.')
        self.client = NS(send_message=MagicMock(return_value={'message_id':123}))

    def process(self, enqueue):
        with patch.object(bot, 'TelegramApiClient', return_value=self.client), \
                patch.object(bot, 'build_reply', return_value=bot.BotReply('Respuesta de prueba.', 'consulta')), \
                patch.object(tts, 'enqueue_reply', side_effect=enqueue):
            return bot.process_next_update()

    def test_text_is_delivered_and_state_saved_before_optional_audio_is_queued(self):
        def enqueue(update, reply, delivery):
            stored = TelegramActualizacion.objects.get(pk=update.pk)
            self.assertEqual((stored.estado, stored.respuesta), ('PROCESADO','Respuesta de prueba.'))
            self.client.send_message.assert_called_once()
            self.assertEqual(delivery, {'message_id':123})
        self.assertTrue(self.process(enqueue))
        self.assertFalse(Egreso.objects.exists())

    def test_queue_failure_does_not_change_success_or_resend_the_text(self):
        with self.assertLogs(bot.logger, level='WARNING') as logs:
            self.assertTrue(self.process(RuntimeError('PRIVATE TEST_ONLY_BOT_TOKEN')))
        self.update.refresh_from_db()
        self.assertEqual((self.update.estado,self.update.intentos,self.update.error), ('PROCESADO',1,''))
        self.client.send_message.assert_called_once()
        self.assertNotIn('PRIVATE', str(logs.output))
        self.assertFalse(bot.process_next_update())

    def test_still_authorized_requires_private_completed_update_and_active_binding(self):
        with patch.object(tts, 'close_old_connections'):
            self.assertFalse(tts._still_authorized(job()))
            self.update.estado = 'PROCESADO'
            self.update.procesado_en = timezone.now()
            self.update.save()
            self.assertTrue(tts._still_authorized(job()))
            self.profile.activo = False
            self.profile.save()
            self.assertFalse(tts._still_authorized(job()))

    def test_worker_announces_voice_mode_without_keys_or_network(self):
        output = io.StringIO()
        command = 'mainApp.management.commands.procesar_telegram_bot.'
        with patch(command + 'process_next_update', return_value=False), \
                patch(command + 'recover_stale_updates', return_value=0), \
                patch(command + 'is_feature_enabled', return_value=True), \
                patch.object(tts, 'status', return_value={'mode':'voice','label':'Audio al recibir una nota de voz'}), \
                patch.object(tts, 'http_request') as http:
            call_command('procesar_telegram_bot', once=True, stdout=output)
        self.assertIn('Voz de salida: voice | Audio al recibir una nota de voz', output.getvalue())
        self.assertNotIn('GEMINI_API_KEY', output.getvalue())
        http.assert_not_called()
