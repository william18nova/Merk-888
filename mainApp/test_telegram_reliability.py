"""Fallos simulados; no redes, trabajadores reales, pagos ni claves de producción."""
import json
import time
from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .models import Rol, TelegramActualizacion, TelegramUsuario, Usuario
from .services import telegram_bot as bot, telegram_transport as transport, telegram_voice as voice


def http(status=200, data=None, *, headers=None, chunks=(b'ogg',)):
    return SimpleNamespace(status_code=status, ok=200 <= status < 300,
        json=MagicMock(return_value=data if data is not None else {'ok': True, 'result': {}}),
        headers=headers or {}, close=MagicMock(), iter_content=MagicMock(return_value=iter(chunks)))


@override_settings(TELEGRAM_BOT_TOKEN='TEST_ONLY_TOKEN')
class TelegramTransportReliabilityTests(SimpleTestCase):
    def test_rate_limit_uses_longest_server_wait_and_closes_connection(self):
        response = http(429, {'ok': False, 'parameters': {'retry_after': 45}}, headers={'Retry-After': '60'})
        with patch.object(bot.requests, 'post', return_value=response) as post:
            with self.assertRaises(bot.TelegramRateLimit) as failure:
                bot.TelegramApiClient()._post('sendMessage')
        self.assertEqual(failure.exception.retry_after, 60)
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        self.assertEqual(post.call_args.kwargs['timeout'], (3, 20))
        response.close.assert_called_once()

    def test_rate_limit_in_api_body_is_respected_even_when_http_is_200(self):
        with patch.object(bot.requests, 'post', return_value=http(200, {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 20}})):
            with self.assertRaises(bot.TelegramRateLimit) as failure:
                bot.TelegramApiClient()._post('getFile')
        self.assertEqual(failure.exception.retry_after, 20)

    def test_invalid_retry_after_is_bounded_and_safe(self):
        for value in [None, True, 'bad', float('nan'), float('inf'), -1, {}, []]:
            with self.subTest(value=value):
                self.assertEqual(transport.retry_after({'parameters': {'retry_after': value}}), 30)
        self.assertEqual(transport.retry_after({'parameters': {'retry_after': 1e200}}), 86400)
        self.assertEqual(transport.retry_after({}, {'Retry-After': '1.2'}), 2)

    def test_read_timeout_and_connection_drop_are_uncertain_not_replayed(self):
        for error in [requests.ReadTimeout('PRIVATE TOKEN'), requests.ConnectionError('PRIVATE TOKEN')]:
            with self.subTest(error=type(error).__name__), patch.object(bot.requests, 'post', side_effect=error) as post:
                with self.assertRaises(bot.TelegramDeliveryUncertain) as failure:
                    bot.TelegramApiClient().send_message(1, 'PRIVATE MESSAGE')
                self.assertFalse(failure.exception.retryable)
                self.assertNotIn('PRIVATE', str(failure.exception))
                post.assert_called_once()

    def test_connect_timeout_before_sending_is_safe_to_defer(self):
        with patch.object(bot.requests, 'post', side_effect=requests.ConnectTimeout('PRIVATE')):
            with self.assertRaises(bot.TelegramSendDeferred) as failure:
                bot.TelegramApiClient().send_message(1, 'DEMO')
        self.assertTrue(failure.exception.retryable)

    def test_server_error_invalid_json_and_redirect_do_not_repeat_send(self):
        for response in [http(503), http(302), http(200, ['invalid'])]:
            with self.subTest(status=response.status_code), patch.object(bot.requests, 'post', return_value=response) as post:
                with self.assertRaises(bot.TelegramDeliveryUncertain):
                    bot.TelegramApiClient().send_message(1, 'DEMO')
                post.assert_called_once()
                response.close.assert_called_once()

    def test_short_rate_limit_retries_same_payload_not_the_operation(self):
        client = bot.TelegramApiClient()
        with patch.object(client, '_post', side_effect=[bot.TelegramRateLimit(2), {'message_id': 1}]) as post, \
                patch.object(bot.time, 'monotonic', return_value=100), patch.object(bot.time, 'sleep') as sleep:
            result = client.send_message(1, 'Respuesta DEMO')
        self.assertEqual(result, {'message_id': 1})
        self.assertEqual(post.call_args_list[0], post.call_args_list[1])
        sleep.assert_called_once_with(2)

    def test_long_rate_limit_returns_to_queue_without_blocking_worker(self):
        client = bot.TelegramApiClient()
        with patch.object(client, '_post', side_effect=bot.TelegramRateLimit(120)) as post, patch.object(bot.time, 'sleep') as sleep:
            with self.assertRaises(bot.TelegramRateLimit):
                client.send_message(1, 'DEMO')
        post.assert_called_once()
        sleep.assert_not_called()

    def test_partial_reply_is_not_replayed_and_buttons_stay_on_final_chunk(self):
        client = bot.TelegramApiClient()
        markup = {'inline_keyboard': [[{'text': 'Confirmar', 'callback_data': 'confirm:demo'}]]}
        with patch.object(client, '_post', side_effect=[{}, bot.TelegramRateLimit(120)]) as post, patch.object(bot.time, 'sleep'):
            with self.assertRaises(bot.TelegramDeliveryUncertain):
                client.send_message(1, '😀' * 2000, reply_markup=markup, require_buttons=True)
        self.assertNotIn('reply_markup', post.call_args_list[0].kwargs['payload'])
        self.assertEqual(post.call_args_list[1].kwargs['payload']['reply_markup'], markup)
        self.assertEqual(post.call_count, 2)

    def test_callback_ack_failure_never_blocks_the_real_result(self):
        client = bot.TelegramApiClient()
        with patch.object(client, '_post', side_effect=bot.TelegramBotError('expired')):
            self.assertIsNone(client.answer_callback('old-button'))

    def test_callback_ack_has_short_timeout_and_cleanup_cannot_mask_success(self):
        client = bot.TelegramApiClient()
        with patch.object(client, '_post', return_value=True) as post:
            self.assertTrue(client.answer_callback('button'))
        self.assertEqual(post.call_args.kwargs['timeout'], (2, 3))
        response = http()
        response.close.side_effect = OSError('PRIVATE TOKEN')
        with patch.object(bot.requests, 'post', return_value=response), self.assertLogs(bot.logger, level='WARNING') as logs:
            self.assertEqual(client._post('sendMessage'), {})
        self.assertNotIn('PRIVATE', str(logs.output))

    def test_invalid_json_is_uncertain_and_connection_is_closed(self):
        response = http()
        response.json.side_effect = ValueError('PRIVATE BODY')
        with patch.object(bot.requests, 'post', return_value=response):
            with self.assertRaises(bot.TelegramDeliveryUncertain) as failure:
                bot.TelegramApiClient()._post('sendMessage')
        response.close.assert_called_once()
        self.assertNotIn('PRIVATE', str(failure.exception))

    def test_download_streams_closes_and_never_follows_redirects(self):
        response = http(chunks=[b'ab', b'cd'])
        with patch.object(transport.requests, 'get', return_value=response) as get:
            self.assertEqual(transport.download_voice_file('TOKEN', {'file_path': 'voice/file_1.oga'}, limit=5), b'abcd')
        self.assertTrue(get.call_args.kwargs['stream'])
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        response.close.assert_called_once()

    def test_download_rejects_overflow_before_reading_more(self):
        response = http(chunks=[b'abc', b'def'])
        with patch.object(transport.requests, 'get', return_value=response):
            with self.assertRaisesMessage(bot.TelegramBotError, '20 MB'):
                transport.download_voice_file('TOKEN', {'file_path': 'voice/file.oga'}, limit=5)
        response.close.assert_called_once()

    def test_download_checks_declared_length_empty_body_and_redirect(self):
        for response in [http(headers={'Content-Length': '100'}), http(chunks=[]), http(302)]:
            with self.subTest(response=response.status_code), patch.object(transport.requests, 'get', return_value=response):
                with self.assertRaises(bot.TelegramBotError):
                    transport.download_voice_file('TOKEN', {'file_path': 'voice/file.oga'}, limit=5)
                response.close.assert_called_once()

    def test_download_rejects_untrusted_path_and_metadata_before_network(self):
        bad = [{'file_path': path} for path in ['https://evil.invalid/a', '/voice/a', 'voice/../a', 'voice/a?token=x', 'voice/%2e%2e/a']]
        bad += [[], {'file_path': 'voice/a', 'file_size': -1}, {'file_path': 'voice/a', 'file_size': True}]
        with patch.object(transport.requests, 'get') as get:
            for result in bad:
                with self.subTest(result=result), self.assertRaises(bot.TelegramBotError):
                    transport.download_voice_file('TOKEN', result, limit=5)
        get.assert_not_called()

    def test_download_deadline_and_network_exception_are_safe(self):
        response = http(chunks=[b'a'])
        with patch.object(transport.time, 'monotonic', side_effect=[100, 146]), patch.object(transport.requests, 'get', return_value=response):
            with self.assertRaises(bot.TelegramExternalError):
                transport.download_voice_file('TOKEN', {'file_path': 'voice/file.oga'}, limit=5)
        response.close.assert_called_once()
        with patch.object(transport.requests, 'get', side_effect=requests.Timeout('TOKEN PRIVATE')):
            with self.assertRaises(bot.TelegramExternalError) as failure:
                transport.download_voice_file('TOKEN', {'file_path': 'voice/file.oga'}, limit=5)
        self.assertNotIn('PRIVATE', str(failure.exception))
        self.assertNotIn('TOKEN', str(failure.exception))

    def test_download_cleanup_failure_does_not_discard_valid_audio(self):
        response = http()
        response.close.side_effect = OSError('PRIVATE TOKEN')
        with patch.object(transport.requests, 'get', return_value=response), \
                self.assertLogs(transport.logger, level='WARNING') as logs:
            self.assertEqual(transport.download_voice_file('TOKEN', {'file_path': 'voice/file.oga'}, limit=5), b'ogg')
        self.assertNotIn('PRIVATE', str(logs.output))

    def test_webhook_rejects_malformed_structures_and_integer_overflow(self):
        bad = [{'update_id': value} for value in [True, '1', -1, 2**63, [], {}]]
        bad += [{'update_id': 1, 'callback_query': []}, {'update_id': 1, 'message': {'from': []}},
                {'update_id': 1, 'message': {'chat': {'id': 'x'}}},
                {'update_id': 1, 'message': {'from': {'id': -1}}},
                {'update_id': 1, 'message': {'voice': {'file_size': -1}}}]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(bot.TelegramBotError):
                bot.normalize_webhook_update(payload)


class TelegramQueueReliabilityTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user('DEMO fiabilidad', rolid=Rol.objects.create(nombre='Web Master'))
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=1, telegram_chat_id=1)
        self.update = TelegramActualizacion.objects.create(update_id=81001, telegram_user_id=1, telegram_chat_id=1,
            chat_type='private', tipo='TEXTO', texto='/estado')
        self.client = SimpleNamespace(send_message=MagicMock())

    def run_update(self, *, reply=None, failure=None):
        with patch.object(bot, 'is_feature_enabled', return_value=True), patch.object(bot, 'TelegramApiClient', return_value=self.client), \
                patch.object(bot, 'build_reply', side_effect=failure, return_value=reply or bot.BotReply('DEMO', 'consulta')):
            return bot.process_next_update()

    def test_temporary_failure_is_delayed_and_other_chats_can_progress(self):
        before = timezone.now()
        self.run_update(failure=bot.TelegramExternalError('PRIVATE'))
        self.update.refresh_from_db()
        self.assertEqual(self.update.estado, 'PENDIENTE')
        self.assertGreaterEqual(self.update.reintentar_en, before+timedelta(seconds=5))
        other = TelegramActualizacion.objects.create(update_id=81002, telegram_user_id=2, telegram_chat_id=2,
            chat_type='private', tipo='TEXTO', texto='DEMO')
        self.run_update()
        other.refresh_from_db()
        self.assertEqual(other.estado, 'PROCESADO')
        self.update.refresh_from_db()
        self.assertEqual(self.update.intentos, 1)

    def test_rate_limit_never_retries_before_requested_pause(self):
        self.client.send_message.side_effect = bot.TelegramRateLimit(120)
        before = timezone.now()
        self.run_update()
        self.update.refresh_from_db()
        self.assertEqual(self.update.estado, 'PENDIENTE')
        self.assertGreaterEqual(self.update.reintentar_en, before+timedelta(seconds=120))
        self.assertFalse(self.run_update())
        self.assertEqual(self.client.send_message.call_count, 1)

    def test_exhausted_rate_limit_does_not_send_another_error_message_during_pause(self):
        TelegramActualizacion.objects.filter(pk=self.update.pk).update(intentos=2)
        self.client.send_message.side_effect = bot.TelegramRateLimit(120)
        self.run_update()
        self.update.refresh_from_db()
        self.assertEqual(self.update.estado, 'ERROR')
        self.client.send_message.assert_called_once()

    def test_secret_bubble_rate_limit_never_retries_or_sends_during_pause(self):
        self.client.send_message.side_effect = bot.TelegramRateLimit(120)
        self.run_update(reply=bot.BotReply('Referencia DEMO', 'clave_merk2_generada',
            history_text='Referencia DEMO sin clave', copyable_code='00001234'))
        self.update.refresh_from_db()
        self.assertEqual(self.update.estado, 'ERROR')
        self.assertNotIn('00001234', self.update.respuesta+self.update.error)
        self.client.send_message.assert_called_once()

    def test_retry_budget_stops_at_three_and_clears_scheduling(self):
        for attempt in range(1, 4):
            TelegramActualizacion.objects.filter(pk=self.update.pk).update(reintentar_en=None)
            self.run_update(failure=bot.TelegramExternalError('PRIVATE'))
            self.update.refresh_from_db()
            self.assertEqual(self.update.intentos, attempt)
        self.assertEqual(self.update.estado, 'ERROR')
        self.assertIsNone(self.update.reintentar_en)
        self.assertNotIn('PRIVATE', self.update.error)
        self.assertFalse(self.run_update())

    def test_unknown_delivery_failure_keeps_result_without_reexecuting(self):
        self.client.send_message.side_effect = [bot.TelegramExternalError('PRIVATE'), None]
        self.run_update(reply=bot.BotReply('Resultado seguro de DEMO', 'callback_confirm'))
        self.update.refresh_from_db()
        self.assertEqual(self.update.estado, 'ERROR')
        self.assertEqual(self.update.respuesta, 'Resultado seguro de DEMO')
        self.assertFalse(self.run_update())
        self.assertNotIn('PRIVATE', self.update.error)

    def test_diagnostics_never_store_or_log_unexpected_exception_contents(self):
        with self.assertLogs(bot.logger, level='ERROR') as logs:
            self.run_update(failure=RuntimeError('PRIVATE_SQL TOKEN SECRET_DATA'))
        self.update.refresh_from_db()
        self.assertIn('RuntimeError', self.update.error)
        self.assertNotIn('PRIVATE_SQL', self.update.error+str(logs.output))
        self.assertNotIn('SECRET_DATA', self.update.error+str(logs.output))

    def test_stale_confirmed_result_is_not_automatically_reexecuted(self):
        TelegramActualizacion.objects.filter(pk=self.update.pk).update(estado='PROCESANDO', intentos=1,
            iniciado_en=timezone.now()-timedelta(minutes=11), intencion='clave_merk2_generada', respuesta='Referencia sin clave')
        bot.recover_stale_updates()
        self.update.refresh_from_db()
        self.assertEqual(self.update.estado, 'ERROR')
        self.assertFalse(self.run_update())

    def test_invalid_secret_reply_is_rejected_before_any_plaintext_persistence(self):
        for number, reply in enumerate([
            bot.BotReply('12345678', 'clave_merk2_generada', copyable_code='12345678'),
            bot.BotReply('Referencia DEMO', 'clave_merk2_generada', history_text='12345678', copyable_code='12345678'),
        ], start=81010):
            with self.subTest(number=number):
                TelegramActualizacion.objects.filter(pk=self.update.pk).update(estado='ERROR')
                self.update = TelegramActualizacion.objects.create(update_id=number, telegram_user_id=1, telegram_chat_id=1,
                    chat_type='private', tipo='CALLBACK', texto='confirm:DEMO')
                self.run_update(reply=reply)
                self.update.refresh_from_db()
                self.assertEqual(self.update.estado, 'ERROR')
                self.assertNotIn('12345678', self.update.respuesta+self.update.error)
                self.assertNotIn('12345678', str(self.client.send_message.call_args_list))

    def test_async_resume_does_not_download_audio_again(self):
        TelegramActualizacion.objects.filter(pk=self.update.pk).update(tipo='VOZ',
            transcripcion_estado={'assemblyai': {'id': 'job-demo', 'started': time.time()}})
        self.client.download_voice = MagicMock()
        with patch.object(bot, 'transcribe_voice', return_value='/estado') as transcribe:
            self.run_update()
        self.client.download_voice.assert_not_called()
        self.assertIsNone(transcribe.call_args.args[0])
        self.update.refresh_from_db()
        self.assertEqual(self.update.estado, 'PROCESADO')

    def test_doctor_is_read_only_and_does_not_expose_messages_or_ids(self):
        before = list(TelegramActualizacion.objects.values())
        output = StringIO()
        with patch.object(bot.requests, 'post') as post, patch.object(bot.requests, 'get') as get:
            call_command('diagnosticar_telegram_bot', as_json=True, stdout=output)
        result = json.loads(output.getvalue())
        self.assertTrue(result['solo_lectura'])
        self.assertEqual(result['pendientes_listos'], 1)
        self.assertNotIn('/estado', output.getvalue())
        self.assertNotIn('81001', output.getvalue())
        self.assertEqual(before, list(TelegramActualizacion.objects.values()))
        post.assert_not_called()
        get.assert_not_called()

    def test_connection_cleanup_never_closes_the_callers_atomic_transaction(self):
        from django.db import connection
        self.assertTrue(connection.in_atomic_block)
        with patch.object(bot, 'close_old_connections') as close:
            bot._close_bot_connections()
        close.assert_not_called()


@override_settings(ASSEMBLYAI_API_KEY='TEST_ONLY', ASSEMBLYAI_TRIAL_CONFIRMED='true', TELEGRAM_VOICE_PROVIDERS='assemblyai')
class TelegramVoiceResumeReliabilityTests(SimpleTestCase):
    def setUp(self):
        voice._FAILURES.clear()
        self.addCleanup(voice._FAILURES.clear)
        self.update = SimpleNamespace(transcripcion_estado={'assemblyai': {'id': 'job-demo', 'started': time.time()}},
            transcripcion='', save=MagicMock())

    def test_existing_job_needs_no_audio_and_only_gets_polled(self):
        self.assertFalse(voice.needs_audio(self.update))
        with patch.object(voice, '_request', side_effect=[{'status': 'completed', 'text': 'Ventas de hoy'}, {}]) as request:
            self.assertEqual(voice.transcribe(None, update=self.update), 'Ventas de hoy')
        self.assertEqual([call.args[1] for call in request.call_args_list], ['get', 'delete'])
        self.assertEqual(self.update.transcripcion, 'Ventas de hoy')

    def test_temporary_polling_failure_keeps_same_job_and_does_not_upload(self):
        with patch.object(voice, '_request', side_effect=bot.TelegramAIProviderError('AssemblyAI', 'connection', delay=10)) as request:
            with self.assertRaises(bot.TelegramTranscriptionPending):
                voice.transcribe(None, update=self.update)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(request.call_args.args[1], 'get')
        self.assertEqual(self.update.transcripcion_estado['assemblyai']['id'], 'job-demo')
        self.assertNotIn('assemblyai', self.update.transcripcion_estado.get('attempted', []))

    def test_expired_job_requests_complete_audio_for_fallback(self):
        self.update.transcripcion_estado['assemblyai']['started'] = time.time()-601
        self.assertTrue(voice.needs_audio(self.update))

    def test_corrupt_resume_state_is_safe_and_does_not_contact_provider(self):
        for state in [['bad'], {'attempted': 'bad'}, {'assemblyai': {'id': 'job-demo', 'started': 'bad'}},
                      {'assemblyai': {'id': 'job-demo', 'started': 1e200}}]:
            self.update.transcripcion_estado = state
            with self.subTest(state=state), patch.object(voice.requests, 'get') as get, patch.object(voice.requests, 'post') as post:
                with self.assertRaises(bot.TelegramBotError):
                    voice.transcribe(None, update=self.update)
                get.assert_not_called()
                post.assert_not_called()


class TelegramWorkerRecoveryReliabilityTests(SimpleTestCase):
    module = 'mainApp.management.commands.procesar_telegram_bot.'

    def test_startup_database_failure_is_recovered_in_same_worker(self):
        stderr = StringIO()
        with patch(self.module+'recover_stale_updates', side_effect=[DatabaseError('PRIVATE'), 0]) as recovery, \
                patch(self.module+'is_feature_enabled', return_value=True), patch(self.module+'close_old_connections'), \
                patch(self.module+'process_next_update', return_value=True) as process, patch(self.module+'time.sleep') as sleep:
            call_command('procesar_telegram_bot', max_messages=1, stdout=StringIO(), stderr=stderr)
        self.assertEqual(recovery.call_count, 2)
        process.assert_called_once()
        sleep.assert_called_once_with(5)
        self.assertNotIn('PRIVATE', stderr.getvalue())

    def test_repeated_database_failures_back_off_and_then_recover(self):
        stderr = StringIO()
        with patch(self.module+'recover_stale_updates', side_effect=[DatabaseError('PRIVATE')]*3+[0]), \
                patch(self.module+'is_feature_enabled', return_value=True), patch(self.module+'close_old_connections'), \
                patch(self.module+'process_next_update', return_value=True), patch(self.module+'time.sleep') as sleep:
            call_command('procesar_telegram_bot', max_messages=1, stdout=StringIO(), stderr=stderr)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 10, 20])
        self.assertNotIn('PRIVATE', stderr.getvalue())

    def test_unexpected_cycle_error_does_not_kill_worker_or_show_private_data(self):
        stderr = StringIO()
        with patch(self.module+'recover_stale_updates'), patch(self.module+'is_feature_enabled', return_value=True), \
                patch(self.module+'close_old_connections'), patch(self.module+'process_next_update', side_effect=[RuntimeError('PRIVATE'), True]), \
                patch(self.module+'time.sleep') as sleep:
            call_command('procesar_telegram_bot', max_messages=1, stdout=StringIO(), stderr=stderr)
        sleep.assert_called_once_with(10)
        self.assertNotIn('PRIVATE', stderr.getvalue())

    def test_once_fails_safely_without_looping_on_database_outage(self):
        with patch(self.module+'recover_stale_updates', side_effect=DatabaseError('PRIVATE')), \
                patch(self.module+'is_feature_enabled', return_value=True), patch(self.module+'close_old_connections'), \
                patch(self.module+'time.sleep') as sleep:
            with self.assertRaises(CommandError) as failure:
                call_command('procesar_telegram_bot', once=True, stdout=StringIO(), stderr=StringIO())
        self.assertNotIn('PRIVATE', str(failure.exception))
        sleep.assert_not_called()

    def test_nonfinite_sleep_is_rejected(self):
        for value in [float('nan'), float('inf'), float('-inf')]:
            with self.subTest(value=value), self.assertRaises(CommandError):
                call_command('procesar_telegram_bot', sleep=value, once=True, stdout=StringIO())


@override_settings(TELEGRAM_WEBHOOK_SECRET='TEST_ONLY_SECRET')
class TelegramWebhookReliabilityTests(SimpleTestCase):
    def test_malformed_and_oversized_webhooks_never_touch_queue(self):
        from .views import TelegramWebhookView
        cases = [(json.dumps({'update_id': 1, 'message': {'from': ['bad']}}), 400),
                 (json.dumps({'update_id': 2**80}), 400),
                 (json.dumps({'update_id': 1, 'message': {'voice': {'file_size': -1}}}), 400),
                 # JSON puede alcanzar el validador o el límite del decodificador,
                 # según la profundidad de pila disponible. Ambos rechazan sin BD.
                 ('['*1200+'0'+']'*1200, (400, 413)), ('x'*262145, 413)]
        with patch('mainApp.views.is_feature_enabled', return_value=True), \
                patch('mainApp.views.TelegramActualizacion.objects.get_or_create') as enqueue:
            for body, expected in cases:
                request = RequestFactory().post('/api/telegram/webhook/', data=body, content_type='application/json',
                    HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='TEST_ONLY_SECRET')
                with self.subTest(expected=expected):
                    response = TelegramWebhookView.as_view()(request)
                    self.assertIn(response.status_code, expected if isinstance(expected, tuple) else (expected,))
        enqueue.assert_not_called()

    def test_decoder_recursion_error_is_a_safe_rejection(self):
        from .views import TelegramWebhookView
        request = RequestFactory().post('/api/telegram/webhook/', data='{}', content_type='application/json',
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN='TEST_ONLY_SECRET')
        with patch('mainApp.views.is_feature_enabled', return_value=True), \
                patch('mainApp.views.json.loads', side_effect=RecursionError('PRIVATE')):
            response = TelegramWebhookView.as_view()(request)
        self.assertEqual(response.status_code, 413)
        self.assertNotIn(b'PRIVATE', response.content)

    def test_doctor_database_failure_has_safe_message(self):
        with patch('mainApp.management.commands.diagnosticar_telegram_bot.TelegramActualizacion.objects.all',
                   side_effect=DatabaseError('PRIVATE_PASSWORD SQL')):
            with self.assertRaises(CommandError) as failure:
                call_command('diagnosticar_telegram_bot', stdout=StringIO())
        self.assertNotIn('PRIVATE_PASSWORD', str(failure.exception))
