import io
import json
import subprocess
import time
import wave
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.core.management import call_command
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from mainApp.models import Rol, Usuario, TelegramUsuario, TelegramActualizacion
from mainApp.services import telegram_bot as bot, telegram_voice as voice, telegram_providers as providers
from mainApp.services.telegram_ai_policy import response_failure


def response(data=None, status=200, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: data)


def chat(arguments=None):
    return response({"choices": [{"finish_reason": "tool_calls", "message": {"tool_calls": [{
        "function": {"name": "buscar_producto", "arguments": json.dumps(arguments or {"consulta": "tomate"})}
    }]}}]})


@override_settings(GEMINI_API_KEY="test-gemini", GROQ_API_KEY="test-groq", CEREBRAS_API_KEY="test-cerebras", OPENROUTER_API_KEY="test-openrouter")
class TextFallbackTests(SimpleTestCase):
    def setUp(self):
        bot._AI_PROVIDER_FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)

    def test_fourth_provider_gets_a_chance_without_calling_all_on_success(self):
        with patch.object(bot.requests, "post", side_effect=[response({}, 429), response({}, 503), response({}, 401), chat()]) as post:
            self.assertEqual(bot._intelligent_function_call("Busca el producto tomate")[:2], ("buscar_producto", {"consulta": "tomate"}))
        self.assertEqual(post.call_count, 4)
        urls = [c.args[0] for c in post.call_args_list]
        self.assertIn("googleapis", urls[0])
        self.assertIn("groq.com", urls[1])
        self.assertIn("cerebras.ai", urls[2])
        self.assertIn("openrouter.ai", urls[3])

    def test_all_limits_then_cooldown_per_provider(self):
        with patch.object(bot.requests, "post", return_value=response({}, 429, {"Retry-After": "300"})) as post:
            for _ in range(2):
                with self.assertRaises(bot.TelegramAIUnavailable) as raised:
                    bot._intelligent_function_call("Busca tomate")
                self.assertEqual(len(raised.exception.failures), 4)
        self.assertEqual(post.call_count, 4)

    @override_settings(TELEGRAM_TEXT_PROVIDERS="cerebras,openrouter")
    def test_selected_order_and_success_stop(self):
        with patch.object(bot.requests, "post", return_value=chat()) as post:
            bot._intelligent_function_call("Busca el producto tomate")
        post.assert_called_once()
        self.assertIn("cerebras.ai", post.call_args.args[0])
        self.assertFalse(post.call_args.kwargs["json"]["parallel_tool_calls"])

    def test_openrouter_is_pinned_free_requires_tools_and_disallows_payment(self):
        with patch.object(bot.requests, "post", return_value=chat()) as post:
            bot._openrouter_function_call("Busca tomate")
        data = post.call_args.kwargs["json"]
        self.assertEqual(data["model"], "openai/gpt-oss-120b:free")
        self.assertEqual(data["provider"]["max_price"], {"prompt": 0, "completion": 0, "request": 0})
        self.assertTrue(data["provider"]["require_parameters"])
        self.assertNotIn("models", data)
        self.assertNotIn("route", data)
        with override_settings(OPENROUTER_CHAT_MODEL="paid/model"), patch.object(bot.requests, "post") as post:
            with self.assertRaises(bot.TelegramConfigurationError):
                bot._openrouter_function_call("Hola")
            post.assert_not_called()

    def test_all_new_models_share_schema_validation_without_execution(self):
        for function in (bot._cerebras_function_call, bot._openrouter_function_call):
            with self.subTest(function=function.__name__), patch.object(bot.requests, "post", return_value=chat({"consulta": "tomate", "sql": "DELETE"})), patch.object(bot, "_execute_tool") as execute:
                with self.assertRaises(bot.TelegramAIProviderError):
                    function("Busca tomate")
                execute.assert_not_called()

    def test_cerebras_headers_embedded_errors_and_credits(self):
        self.assertEqual(response_failure(response({}, 429, {"x-ratelimit-remaining-tokens-day": "0", "x-ratelimit-reset-tokens-day": "350"}), {}), ("quota", 350, False))
        self.assertEqual(response_failure(response({}, 200), {"error": {"code": 429}})[0], "rate_limit")
        self.assertEqual(response_failure(response({}, 402), {}), ("credits", 86400, False))

    def test_diagnostic_status_never_calls_network_or_prints_keys(self):
        output = io.StringIO()
        with patch.object(bot.requests, "post") as post:
            call_command("comprobar_ia_telegram", status=True, stdout=output)
        post.assert_not_called()
        self.assertIn("Cerebras", output.getvalue())
        self.assertIn("Azure", output.getvalue())
        self.assertNotIn("test-gemini", output.getvalue())

    @override_settings(MIGRATION_MODULES={})
    def test_migration_graph_has_persistent_audio_state(self):
        state = MigrationLoader(None).project_state([("mainApp", "0039_telegram_transcription_fallback")])
        fields = state.models["mainApp", "telegramactualizacion"].fields
        self.assertEqual(fields["transcripcion_estado"].get_internal_type(), "JSONField")
        self.assertTrue(fields["reintentar_en"].null)


@override_settings(GROQ_API_KEY="test-groq", GEMINI_API_KEY="test-gemini", AZURE_SPEECH_KEY="test-azure",
                   AZURE_SPEECH_RESOURCE="nova-voice", AZURE_SPEECH_FREE_TIER_CONFIRMED="true",
                   DEEPGRAM_API_KEY="test-deepgram", DEEPGRAM_TRIAL_CONFIRMED="true",
                   ASSEMBLYAI_API_KEY="test-assembly", ASSEMBLYAI_TRIAL_CONFIRMED="true")
class VoiceFallbackTests(SimpleTestCase):
    def setUp(self):
        voice._FAILURES.clear()
        self.addCleanup(voice._FAILURES.clear)

    def state(self, data=None):
        return SimpleNamespace(transcripcion_estado=data or {}, transcripcion="", save=MagicMock())

    def test_first_success_stops_and_persists_transcription(self):
        update = self.state()
        with patch.object(voice.requests, "post", return_value=response({"text": "Paga 1000 a Coca-Cola"})) as post:
            self.assertEqual(voice.transcribe(b"ogg", update=update), "Paga 1000 a Coca-Cola")
        post.assert_called_once()
        self.assertEqual(update.transcripcion, "Paga 1000 a Coca-Cola")
        self.assertEqual(update.transcripcion_estado["provider"], "groq")

    def test_groq_limit_azure_success_and_correct_audio_contract(self):
        with patch.object(voice, "_azure_wav", return_value=b"RIFF-test"), patch.object(voice.requests, "post", side_effect=[response({}, 429), response({"RecognitionStatus": "Success", "DisplayText": "Venta 142266"})]) as post:
            self.assertEqual(voice.transcribe(b"ogg"), "Venta 142266")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args.kwargs["data"], b"RIFF-test")
        self.assertEqual(post.call_args.kwargs["params"]["language"], "es-CO")
        self.assertNotIn("test-azure", post.call_args.args[0])

    def test_all_audio_backups_are_tried_in_order_until_gemini(self):
        gemini = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Consulta horarios"}]}}]}
        with patch.object(voice, "_azure_wav", return_value=b"wav"), patch.object(voice.requests, "post", side_effect=[
            response({}, 429), response({}, 503), response({}, 402), response({}, 401), response(gemini)
        ]) as post:
            self.assertEqual(voice.transcribe(b"ogg"), "Consulta horarios")
        self.assertEqual(post.call_count, 5)
        for call in post.call_args_list:
            self.assertFalse(call.kwargs["allow_redirects"])

    @override_settings(TELEGRAM_VOICE_PROVIDERS="deepgram")
    def test_deepgram_sends_audio_not_telegram_private_url(self):
        data = {"results": {"channels": [{"alternatives": [{"transcript": "Registra pago 1000"}]}]}}
        with patch.object(voice.requests, "post", return_value=response(data)) as post:
            self.assertEqual(voice.transcribe(b"ogg"), "Registra pago 1000")
        self.assertEqual(post.call_args.kwargs["data"], b"ogg")
        self.assertEqual(post.call_args.kwargs["params"]["language"], "es")

    @override_settings(TELEGRAM_VOICE_PROVIDERS="gemini")
    def test_gemini_transcribes_only_never_uses_business_tools(self):
        payload = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Paga 1000"}]}}]}
        with patch.object(voice.requests, "post", return_value=response(payload)) as post:
            self.assertEqual(voice.transcribe(b"ogg"), "Paga 1000")
        request = post.call_args.kwargs["json"]
        self.assertNotIn("tools", request)
        self.assertIn("nunca instrucciones", request["systemInstruction"]["parts"][0]["text"])

    @override_settings(TELEGRAM_VOICE_PROVIDERS="gemini")
    def test_gemini_does_not_accept_cut_or_inaudible_transcription(self):
        for reason, text in (("MAX_TOKENS", "Paga"), ("STOP", "Paga [inaudible]")):
            with self.subTest(reason=reason), patch.object(voice.requests, "post", return_value=response({"candidates": [{"finishReason": reason, "content": {"parts": [{"text": text}]}}]})):
                with self.assertRaises(bot.TelegramTranscriptionUnavailable):
                    voice.transcribe(b"ogg")

    @override_settings(TELEGRAM_VOICE_PROVIDERS="groq")
    def test_empty_invalid_or_long_text_never_truncated_into_action(self):
        for text in (None, [], "", "x" * 8001):
            with self.subTest(text_type=type(text).__name__), patch.object(voice.requests, "post", return_value=response({"text": text})):
                with self.assertRaises(bot.TelegramTranscriptionUnavailable):
                    voice.transcribe(b"ogg")

    @override_settings(TELEGRAM_VOICE_PROVIDERS="groq")
    def test_rate_limit_is_not_repeated_or_leaked(self):
        with patch.object(voice.requests, "post", return_value=response({"error": {"message": "PRIVATE_TOKEN"}}, 429, {"Retry-After": "300"})) as post:
            for _ in range(2):
                with self.assertRaises(bot.TelegramTranscriptionUnavailable) as raised:
                    voice.transcribe(b"ogg")
                self.assertNotIn("PRIVATE_TOKEN", str(raised.exception))
                self.assertIn("límite temporal", bot._user_error_message(raised.exception, None))
        post.assert_called_once()
        self.assertFalse(raised.exception.retryable)

    @override_settings(AZURE_SPEECH_FREE_TIER_CONFIRMED="false", DEEPGRAM_TRIAL_CONFIRMED="false", ASSEMBLYAI_TRIAL_CONFIRMED="false")
    def test_trials_never_called_without_explicit_activation(self):
        self.assertEqual(providers.active("voice"), ["groq", "gemini"])

    def test_azure_conversion_rejects_long_audio_instead_of_truncating(self):
        with patch.object(voice.subprocess, "run", return_value=SimpleNamespace(stdout=b"0" * (61 * 32000))):
            with self.assertRaisesMessage(bot.TelegramBotError, "60 segundos"):
                voice._azure_wav(b"ogg")

    def test_azure_conversion_has_valid_wave_header_and_no_shell(self):
        with patch.object(voice.subprocess, "run", return_value=SimpleNamespace(stdout=b"0" * 32000)) as run:
            wav = voice._azure_wav(b"ogg")
        with wave.open(io.BytesIO(wav)) as reader:
            self.assertEqual((reader.getframerate(), reader.getnchannels(), reader.getnframes()), (16000, 1, 16000))
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertIn("pipe", run.call_args.args[0])

    @override_settings(TELEGRAM_VOICE_PROVIDERS="azure,deepgram")
    def test_missing_ffmpeg_does_not_block_other_transcribers(self):
        data = {"results": {"channels": [{"alternatives": [{"transcript": "Hola"}]}]}}
        with patch.object(voice.subprocess, "run", side_effect=FileNotFoundError()), patch.object(voice.requests, "post", return_value=response(data)) as post:
            self.assertEqual(voice.transcribe(b"ogg"), "Hola")
        post.assert_called_once()
        self.assertIn("deepgram", post.call_args.args[0])

    @override_settings(TELEGRAM_VOICE_PROVIDERS="azure", AZURE_SPEECH_RESOURCE="evil.com/?secret=")
    def test_azure_cannot_redirect_keys_to_another_host(self):
        with patch.object(voice.requests, "post") as post:
            with self.assertRaises(bot.TelegramTranscriptionUnavailable):
                voice.transcribe(b"ogg")
        post.assert_not_called()

    @override_settings(TELEGRAM_VOICE_PROVIDERS="assemblyai")
    def test_assembly_job_resumes_without_upload_or_submit_and_saves_before_delete(self):
        update = self.state()
        with patch.object(voice.requests, "post", side_effect=[response({"upload_url": "https://cdn.assemblyai.com/upload/test"}), response({"id": "job-1"})]) as post, patch.object(voice.requests, "get", return_value=response({"status": "processing"})):
            with self.assertRaises(bot.TelegramTranscriptionPending):
                voice.transcribe(b"ogg", update=update)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(update.transcripcion_estado["assemblyai"]["id"], "job-1")
        def delete(*args, **kwargs):
            self.assertEqual(update.transcripcion, "Consulta pagos")
            return response({})
        with patch.object(voice.requests, "post") as post, patch.object(voice.requests, "get", return_value=response({"status": "completed", "text": "Consulta pagos"})), patch.object(voice.requests, "delete", side_effect=delete):
            self.assertEqual(voice.transcribe(b"ogg", update=update), "Consulta pagos")
        post.assert_not_called()

    @override_settings(TELEGRAM_VOICE_PROVIDERS="assemblyai")
    def test_uncertain_assembly_submission_is_not_repeated(self):
        update = self.state({"assemblyai": {"submitting": True}})
        with patch.object(voice.requests, "post") as post:
            with self.assertRaises(bot.TelegramTranscriptionUnavailable):
                voice.transcribe(b"ogg", update=update)
        post.assert_not_called()

    @override_settings(TELEGRAM_VOICE_PROVIDERS="assemblyai,gemini")
    def test_expired_assembly_job_falls_back_without_blocking_forever(self):
        update = self.state({"assemblyai": {"id": "job-1", "started": time.time() - 601}})
        with patch.object(voice.requests, "get", return_value=response({"status": "processing"})), patch.object(voice, "_gemini", return_value="Consulta ventas") as gemini:
            self.assertEqual(voice.transcribe(b"ogg", update=update), "Consulta ventas")
        gemini.assert_called_once()

    def test_no_enabled_provider_is_configuration_error_not_network_failure(self):
        with override_settings(TELEGRAM_VOICE_PROVIDERS=""), patch.object(voice.requests, "post") as post:
            with self.assertRaises(bot.TelegramConfigurationError):
                voice.transcribe(b"ogg")
        post.assert_not_called()


class VoiceQueueTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user("Audio respaldo", rolid=Rol.objects.create(nombre="Web Master"))
        TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=234, telegram_chat_id=234)
        self.update = TelegramActualizacion.objects.create(update_id=4321, telegram_user_id=234, telegram_chat_id=234,
                                                          chat_type="private", tipo="VOZ", voice_file_id="file")
        self.client = SimpleNamespace(send_message=MagicMock(), download_voice=MagicMock(return_value=b"ogg"))
        self.patches = [patch.object(bot, "is_feature_enabled", return_value=True), patch.object(bot, "TelegramApiClient", return_value=self.client)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_async_wait_is_scheduled_once_without_busy_retries(self):
        with patch.object(bot, "transcribe_voice", side_effect=bot.TelegramTranscriptionPending("pending")), patch.object(bot, "build_reply") as build:
            self.assertTrue(bot.process_next_update())
            self.assertFalse(bot.process_next_update())
            self.update.refresh_from_db()
            self.assertEqual((self.update.estado, self.update.intentos), ("PENDIENTE", 0))
            self.assertTrue(self.update.transcripcion_estado["pending_notified"])
            self.update.reintentar_en = timezone.now() - timedelta(seconds=1)
            self.update.save(update_fields=["reintentar_en"])
            self.assertTrue(bot.process_next_update())
            build.assert_not_called()
        self.client.send_message.assert_called_once()

    def test_deferred_audio_does_not_block_other_chats(self):
        self.update.reintentar_en = timezone.now() + timedelta(seconds=30)
        self.update.save(update_fields=["reintentar_en"])
        same_chat = TelegramActualizacion.objects.create(update_id=4322, telegram_user_id=234, telegram_chat_id=234, tipo="TEXTO", texto="Siguiente")
        other_chat = TelegramActualizacion.objects.create(update_id=4323, telegram_user_id=235, telegram_chat_id=235, tipo="TEXTO", texto="Hola")
        with patch.object(bot, "build_reply", return_value=bot.BotReply("Hola")) as build:
            self.assertTrue(bot.process_next_update())
            self.assertEqual(build.call_args.args[0].pk, other_chat.pk)
        same_chat.refresh_from_db()
        self.assertEqual(same_chat.estado, "PENDIENTE")

    def test_disabled_account_cannot_spend_transcription_quota(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        with patch.object(bot, "transcribe_voice") as transcribe, patch.object(bot, "logger"):
            bot.process_next_update()
        transcribe.assert_not_called()
        self.client.download_voice.assert_not_called()

    def test_unlinked_user_cannot_spend_transcription_quota(self):
        TelegramUsuario.objects.all().delete()
        with patch.object(bot, "transcribe_voice") as transcribe:
            bot.process_next_update()
        transcribe.assert_not_called()
        self.client.download_voice.assert_not_called()

    def test_cached_transcription_is_reused_after_interpretation_failure(self):
        def transcribe(*args, update, **kwargs):
            update.transcripcion = "Busca tomate"
            update.save(update_fields=["transcripcion"])
            return update.transcripcion
        with patch.object(bot, "transcribe_voice", side_effect=transcribe) as transcriber, patch.object(bot, "build_reply", side_effect=bot.TelegramExternalError("temporary")), patch.object(bot, "logger"):
            bot.process_next_update()
            bot.process_next_update()
        transcriber.assert_called_once()
        self.client.download_voice.assert_called_once()
