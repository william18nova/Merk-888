"""Pruebas deterministas de latencia: sin servicios externos ni base real."""
from http.client import HTTPMessage
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, override_settings

from .services import telegram_bot as bot, telegram_timing as timing, telegram_voice as voice
from .services import telegram_transport as transport


@override_settings(GEMINI_API_KEY="test", GROQ_API_KEY="test", CEREBRAS_API_KEY="test", OPENROUTER_API_KEY="test")
class TelegramLatencyTests(SimpleTestCase):
    def setUp(self):
        bot._AI_PROVIDER_FAILURES.clear()
        voice._FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)
        self.addCleanup(voice._FAILURES.clear)

    def test_initial_fallback_pass_also_obeys_total_budget(self):
        now = [100.0]
        def slow(*args, **kwargs):
            now[0] += 20
            raise requests.Timeout("PRIVATE")
        with patch.object(bot.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(bot.requests, "post", side_effect=slow) as post, patch.object(bot.time, "sleep") as sleep:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("Consulta algo")
        self.assertEqual(post.call_count, 2)  # Antes: los cuatro proveedores, >80 s.
        sleep.assert_not_called()
        self.assertIsNone(timing.ai_deadline.get())

    def test_timeout_contract_fits_remaining_budget(self):
        token = timing.ai_deadline.set(102)
        self.addCleanup(timing.ai_deadline.reset, token)
        with patch.object(bot.time, "monotonic", return_value=100), \
                patch.object(bot.requests, "post", side_effect=requests.Timeout()) as post:
            with self.assertRaises(bot.TelegramAIProviderError):
                bot._ai_response("Gemini", "https://example.invalid")
        self.assertLessEqual(sum(post.call_args.kwargs['timeout']), 2)

    def test_expired_deadline_never_calls_external_service(self):
        token = timing.ai_deadline.set(100)
        self.addCleanup(timing.ai_deadline.reset, token)
        with patch.object(bot.time, "monotonic", return_value=101), patch.object(bot.requests, "post") as post:
            with self.assertRaises(bot.TelegramAIProviderError):
                bot._ai_response("Gemini", "https://example.invalid")
        post.assert_not_called()

    def test_voice_fallback_is_bounded_and_keeps_audio_intact(self):
        now = [100.0]
        seen = []
        def slow(audio, *args):
            seen.append(audio)
            now[0] += 31
            raise bot.TelegramAIProviderError("Groq", "connection")
        with patch.object(voice.providers, "active", return_value=['groq', 'deepgram', 'gemini']), \
                patch.object(voice.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(voice, "_groq", side_effect=slow), patch.object(voice, "_deepgram", side_effect=slow), \
                patch.object(voice, "_gemini") as final:
            with self.assertRaises(bot.TelegramTranscriptionUnavailable):
                voice.transcribe(b'full-unmodified-audio')
        self.assertEqual(seen, [b'full-unmodified-audio'] * 2)
        final.assert_not_called()

    def test_timing_logs_never_include_exception_message(self):
        with self.assertLogs(timing.logger, level='INFO') as logs:
            with self.assertRaises(ValueError), timing.measured('test', update_id=123):
                raise ValueError('SECRET_MESSAGE')
        self.assertNotIn('SECRET_MESSAGE', str(logs.output))
        self.assertIn('resultado=error', str(logs.output))


class TelegramWorkerPriorityTests(SimpleTestCase):
    def run_worker(self, found):
        module = 'mainApp.management.commands.procesar_telegram_bot.'
        events = []
        before = dict(connection.settings_dict)
        def message():
            self.assertEqual(connection.settings_dict['CONN_MAX_AGE'], 60)
            self.assertTrue(connection.settings_dict['CONN_HEALTH_CHECKS'])
            self.assertIsNotNone(transport._http_session.get())
            events.append('message')
            return found
        with patch(module + 'recover_stale_updates'), patch(module + 'close_old_connections'), \
                patch(module + 'is_feature_enabled', return_value=True), \
                patch(module + 'process_next_update', side_effect=message), \
                patch('mainApp.services.telegram_followups.process_followups', side_effect=lambda **kw: events.append(('followup', kw))) as followups:
            call_command('procesar_telegram_bot', once=True, followups=True, stdout=StringIO())
        self.assertEqual(connection.settings_dict, before)
        self.assertIsNone(transport._http_session.get())
        return events, followups

    def test_chat_has_priority_over_scheduled_reports(self):
        events, followups = self.run_worker(True)
        self.assertEqual(events, ['message'])
        followups.assert_not_called()

    def test_idle_worker_sends_at_most_one_report_before_rechecking_chat(self):
        events, _ = self.run_worker(False)
        self.assertEqual(events, ['message', ('followup', {'limit': 1})])

    def test_polling_default_is_half_a_second(self):
        from .management.commands.procesar_telegram_bot import Command
        parser = Command().create_parser('manage.py', 'procesar_telegram_bot')
        self.assertEqual(parser.parse_args([]).sleep, .5)

    def test_http_session_is_shared_across_messages_and_closed_on_exit(self):
        module = 'mainApp.management.commands.procesar_telegram_bot.'
        sessions = []

        def message():
            sessions.append(transport._http_session.get())
            return True

        session = requests.Session()
        with patch.object(transport.requests, 'Session', return_value=session), \
                patch.object(session, 'close', wraps=session.close) as close, \
                patch(module + 'recover_stale_updates'), patch(module + 'close_old_connections'), \
                patch(module + 'is_feature_enabled', return_value=True), \
                patch(module + 'process_next_update', side_effect=message):
            call_command('procesar_telegram_bot', max_messages=2, stdout=StringIO())
        self.assertEqual(sessions, [session, session])
        self.assertIsNone(transport._http_session.get())
        close.assert_called_once_with()


class TelegramHTTPPoolTests(SimpleTestCase):
    def test_pool_does_not_keep_credentials_follow_redirects_or_enable_retries(self):
        cookie_header = HTTPMessage()
        cookie_header.add_header('Set-Cookie', 'sid=previous-message; Path=/; Secure')
        sent = []

        def send(request, **kwargs):
            sent.append((request, kwargs))
            response = requests.Response()
            response.status_code = 302 if len(sent) == 1 else 200
            response.url, response.request = request.url, request
            response._content = b'{}'
            response._content_consumed = True
            response.raw = SimpleNamespace(_original_response=SimpleNamespace(msg=cookie_header))
            if response.status_code == 302:
                response.headers['Location'] = 'https://redirect.example.invalid/'
            return response

        with transport.pooled_http_session():
            session = transport._http_session.get()
            adapter = session.get_adapter('https://one.example.invalid/')
            self.assertEqual(adapter.max_retries.total, 0)
            with patch.object(adapter, 'send', side_effect=send):
                first = transport.http_request('post', 'https://one.example.invalid/1',
                                               headers={'Authorization': 'Bearer synthetic'}, timeout=(3, 12))
                self.assertEqual(first.status_code, 302)
                self.assertEqual(len(sent), 1)
                self.assertFalse(session.cookies)
                transport.http_request('get', 'https://one.example.invalid/2', timeout=(3, 20))
                transport.http_request('get', 'https://other.example.invalid/3', timeout=(3, 20))
        self.assertEqual(sent[0][0].headers['Authorization'], 'Bearer synthetic')
        self.assertEqual(sent[0][1]['timeout'], (3, 12))
        for request, options in sent[1:]:
            self.assertNotIn('Authorization', request.headers)
            self.assertNotIn('Cookie', request.headers)
            self.assertEqual(options['timeout'], (3, 20))
        self.assertIsNone(transport._http_session.get())
