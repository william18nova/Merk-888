import json
from datetime import date, datetime, timedelta, timezone as datetime_timezone
from email.utils import format_datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from mainApp.models import Rol, TelegramActualizacion, TelegramUsuario, Usuario
from mainApp.services import telegram_ai_policy as policy, telegram_bot as bot
from mainApp.services.telegram_assistant import common_read_request


def response(payload=None, status=200, headers=None):
    return SimpleNamespace(status_code=status, ok=200 <= status < 300, headers=headers or {}, json=lambda: payload)


def gemini(text="Respuesta Gemini"):
    return response({"candidates": [{"content": {"parts": [{"text": text}]}}]})


def groq(text="Respuesta Groq"):
    return response({"choices": [{"message": {"content": text}}]})


@override_settings(GEMINI_API_KEY="fake-gemini", GROQ_API_KEY="fake-groq")
class AIResilienceTests(SimpleTestCase):
    def setUp(self):
        bot._AI_PROVIDER_FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)
        self.sleep = patch.object(bot.time, "sleep").start()
        self.addCleanup(patch.stopall)

    def test_retry_after_and_reset_headers_are_respected(self):
        error = response(status=429, headers={"Retry-After": "2", "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "1m7.5s"})
        self.assertEqual(policy.response_failure(error, {}), ("rate_limit", 67.5, False))
        daily = response(status=429, headers={"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2h3m4s"})
        self.assertEqual(policy.response_failure(daily, {}), ("quota", 7384, False))

    def test_gemini_retry_info_and_daily_quota(self):
        data = {"error": {"details": [
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "42.5s"},
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDay-FreeTier"}]},
        ]}}
        self.assertEqual(policy.response_failure(response(status=429), data), ("quota", 42.5, False))

    def test_bad_retry_headers_are_safe(self):
        for value in ("NaN", "inf", "-20", "not-a-date", "x", None):
            with self.subTest(value=value):
                self.assertEqual(policy.response_failure(response(status=429, headers={"retry-after": value}), {}), ("rate_limit", 60, False))

    def test_retry_after_http_date_uses_utc(self):
        now = datetime(2026, 9, 7, 15, tzinfo=datetime_timezone.utc)
        header = format_datetime(now + timedelta(seconds=45), usegmt=True)
        with patch.object(policy, "datetime") as clock:
            clock.now.return_value = now
            self.assertEqual(policy.response_failure(response(status=429, headers={"Retry-After": header}), {}), ("rate_limit", 45, False))

    def test_malformed_error_details_do_not_break_fallback(self):
        for data in (None, [], {"error": []}, {"error": {"code": [], "details": [{"reason": {}}, None]}}, {"error": {"details": {}}}):
            with self.subTest(data=data):
                self.assertEqual(policy.response_failure(response(status=400), data), ("request", 0, False))

    def test_groq_tool_generation_failure_is_retryable_not_configuration_error(self):
        self.assertEqual(policy.response_failure(response(status=400), {"error": {"code": "tool_use_failed"}}), ("invalid_response", 0, True))

    def test_http_errors_are_classified_even_if_body_is_not_json(self):
        for status, kind in ((429, "rate_limit"), (503, "unavailable"), (401, "authentication"), (403, "authentication"), (404, "model"), (400, "request")):
            bad = response(status=status)
            bad.json = MagicMock(side_effect=ValueError("raw secret data"))
            with self.subTest(status=status), patch.object(bot.requests, "post", return_value=bad):
                with self.assertRaises(bot.TelegramAIProviderError) as raised:
                    bot._gemini_function_call("Hola")
                self.assertEqual(raised.exception.kind, kind)
                self.assertNotIn("secret", str(raised.exception))

    def test_invalid_response_does_not_disable_provider_for_other_messages(self):
        with patch.object(bot.requests, "post", side_effect=[response({}), groq(), gemini()]) as post:
            self.assertEqual(bot._intelligent_function_call("Hola")[2], "Respuesta Groq")
            self.assertNotIn("Gemini", bot._AI_PROVIDER_FAILURES)
            self.assertEqual(bot._intelligent_function_call("Hola de nuevo")[2], "Respuesta Gemini")
        self.assertEqual(post.call_count, 3)

    def test_bad_request_does_not_disable_provider_for_other_prompts(self):
        with patch.object(bot.requests, "post", side_effect=[response(status=413), groq(), gemini()]) as post:
            self.assertEqual(bot._intelligent_function_call("Hola")[2], "Respuesta Groq")
            self.assertNotIn("Gemini", bot._AI_PROVIDER_FAILURES)
            self.assertEqual(bot._intelligent_function_call("Hola de nuevo")[2], "Respuesta Gemini")
        self.assertEqual(post.call_count, 3)

    def test_gemini_invalid_key_in_400_is_configuration_failure(self):
        data = {"error": {"details": [{"reason": "API_KEY_INVALID"}]}}
        self.assertEqual(policy.response_failure(response(status=400), data), ("authentication", 300, False))

    def test_schema_error_uses_other_provider_without_executing_actions(self):
        bad = response({"candidates": [{"content": {"parts": [{"functionCall": {"name": "buscar_producto", "args": {"consulta": "tomate", "sql": "NO"}}}]}}]})
        with patch.object(bot.requests, "post", side_effect=[bad, groq()]), patch.object(bot, "_execute_tool") as execute:
            self.assertEqual(bot._intelligent_function_call("Busca el producto tomate")[2], "Respuesta Groq")
        execute.assert_not_called()

    def test_connection_failure_uses_alternative_first(self):
        with patch.object(bot.requests, "post", side_effect=[requests.Timeout("do not expose me"), groq()]) as post:
            self.assertEqual(bot._intelligent_function_call("Hola")[2], "Respuesta Groq")
        self.assertEqual(post.call_count, 2)
        self.sleep.assert_not_called()
        self.assertEqual(bot._AI_PROVIDER_FAILURES["Gemini"][2].kind, "connection")

    def test_one_short_retry_after_both_transient_failures(self):
        with patch.object(bot.requests, "post", side_effect=[response(status=503), response(status=502), gemini()]) as post:
            self.assertEqual(bot._intelligent_function_call("Hola")[2], "Respuesta Gemini")
        self.assertEqual(post.call_count, 3)
        self.sleep.assert_called_once()
        self.assertLessEqual(self.sleep.call_args.args[0], 0.8)

    def test_attempts_are_bounded_and_worker_must_not_repeat_them(self):
        with patch.object(bot.requests, "post", return_value=response(status=503)) as post:
            with self.assertRaises(bot.TelegramAIUnavailable) as raised:
                bot._intelligent_function_call("Hola")
        self.assertEqual(post.call_count, 3)
        self.assertFalse(raised.exception.retryable)
        self.assertIn("fallo temporal", str(raised.exception))
        self.assertNotIn("fake-gemini", str(raised.exception))

    def test_no_early_retry_when_provider_sets_retry_after(self):
        with patch.object(bot.requests, "post", return_value=response(status=503, headers={"retry-after": "20"})) as post:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("Hola")
        self.assertEqual(post.call_count, 2)
        self.sleep.assert_not_called()

    def test_quota_pause_has_real_reason_and_skips_network_until_expiry(self):
        limited = response(status=429, headers={"retry-after": "12"})
        with patch.object(bot.time, "monotonic", return_value=100) as clock, patch.object(bot.requests, "post", return_value=limited) as post:
            for _ in range(2):
                with self.assertRaises(bot.TelegramAIUnavailable) as raised:
                    bot._intelligent_function_call("Hola")
                self.assertIn("límite de uso", str(raised.exception))
                self.assertIn("12 s", str(raised.exception))
            self.assertEqual(post.call_count, 2)
            clock.return_value = 113
            post.return_value = gemini()
            self.assertEqual(bot._intelligent_function_call("Hola")[2], "Respuesta Gemini")
            self.assertEqual(post.call_count, 3)
        self.sleep.assert_not_called()

    def test_model_or_credentials_are_not_retried_per_message(self):
        with patch.object(bot.requests, "post", side_effect=[response(status=401), response(status=404)]) as post:
            for _ in range(2):
                with self.assertRaises(bot.TelegramAIUnavailable) as raised:
                    bot._intelligent_function_call("Hola")
                self.assertIn("clave", str(raised.exception))
                self.assertIn("modelo", str(raised.exception))
                self.assertIn("Web Master", str(raised.exception))
        self.assertEqual(post.call_count, 2)

    def test_retry_budget_skips_extra_attempt_after_slow_failures(self):
        now = [100]

        def fail(*args, **kwargs):
            now[0] += 20
            raise requests.Timeout()

        with patch.object(bot.time, "monotonic", side_effect=lambda: now[0]), patch.object(bot.requests, "post", side_effect=fail) as post:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("Hola")
        self.assertEqual(post.call_count, 2)
        self.sleep.assert_not_called()

    def test_single_provider_can_recover_invalid_output(self):
        with override_settings(GROQ_API_KEY=""), patch.object(bot.requests, "post", side_effect=[response({}), gemini()]) as post:
            self.assertEqual(bot._intelligent_function_call("Hola")[2], "Respuesta Gemini")
        self.assertEqual(post.call_count, 2)

    def test_context_shrinks_for_payments_without_losing_confirmation_rules(self):
        definitions, prompt, _ = bot._ai_request_context("Analiza los pagos del trimestre por concepto")
        names = {d["name"] for d in definitions}
        self.assertIn("consultar_pagos", names)
        self.assertIn("preparar_registro_pago", names)
        self.assertNotIn("preparar_turno_empleado", names)
        small = len(json.dumps(definitions, ensure_ascii=False)) + len(prompt)
        full = len(json.dumps(bot.GEMINI_TOOLS[0]["functionDeclarations"], ensure_ascii=False)) + len(bot._assistant_system_prompt())
        self.assertLess(small, full * 0.6)
        self.assertIn("botón Confirmar", prompt)
        self.assertIn("conceptos parecidos", prompt)

    def test_unknown_request_keeps_all_capabilities(self):
        definitions, prompt, _ = bot._ai_request_context("¿Puedes ayudarme con algo diferente?")
        self.assertEqual({d["name"] for d in definitions}, set(bot.TOOL_FUNCTIONS))
        self.assertEqual(prompt, bot._assistant_system_prompt())

    def test_mixed_questions_keep_both_domains_and_multi_read_tool(self):
        names = policy.selected_tool_names("Compara los pagos con las ventas de los productos", [], bot.TOOL_FUNCTIONS)
        self.assertTrue({"consultar_varias", "consultar_pagos", "consultar_ventas", "buscar_producto"} <= names)

    def test_continuations_keep_previous_domain_and_rotation_safety(self):
        definitions, prompt, history = bot._ai_request_context("Ahora solo esa fecha", [{"role": "user", "text": "Cambia el horario de Camila"}])
        self.assertIn("preparar_turno_empleado", {d["name"] for d in definitions})
        self.assertIn("PREGUNTA alcance", prompt)
        self.assertTrue(history)

    def test_history_budget_preserves_recent_messages_without_mutating_input(self):
        original = [{"role": "user", "text": str(i) * 2000} for i in range(9)]
        result = policy.compact_history(original)
        self.assertLessEqual(sum(len(i["text"]) for i in result), 6000)
        self.assertTrue(result[-1]["text"].startswith("8"))
        self.assertEqual(len(original[-1]["text"]), 2000)

    def test_both_providers_send_same_reduced_tools_without_global_mutation(self):
        original = json.dumps(bot.GEMINI_TOOLS)
        with patch.object(bot.requests, "post", side_effect=[gemini(), groq()]) as post:
            bot._gemini_function_call("Consulta los pagos por concepto")
            bot._groq_function_call("Consulta los pagos por concepto")
        first, second = [call.kwargs["json"] for call in post.call_args_list]
        names = {d["name"] for d in first["tools"][0]["functionDeclarations"]}
        self.assertEqual(names, {d["function"]["name"] for d in second["tools"]})
        self.assertEqual(json.dumps(bot.GEMINI_TOOLS), original)


class NaturalLanguageShortcutTests(SimpleTestCase):
    def setUp(self):
        patcher = patch("mainApp.services.telegram_assistant.timezone.localdate", return_value=date(2026, 9, 7))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_common_payments_keep_explicit_detail_and_breakdown(self):
        for text, detail, breakdown in (
            ("¿Cuánto he pagado hoy?", False, False),
            ("Dame el total de los pagos de ayer", False, False),
            ("Muéstrame los pagos de hoy", True, False),
            ("Cuánto pagué este mes por método de pago", False, True),
            ("Dame los pagos de hoy por medio de pago", True, True),
        ):
            with self.subTest(text=text):
                tool, arguments = common_read_request(text)
                self.assertEqual(tool, "consultar_pagos")
                self.assertEqual(arguments["detalle"], detail)
                self.assertEqual(arguments["desglose_por_medio"], breakdown)

    def test_method_filter_does_not_imply_breakdown(self):
        _, args = common_read_request("Cuánto he pagado hoy en Nequi")
        self.assertEqual(args["medio_pago"], "nequi")
        self.assertFalse(args["desglose_por_medio"])

    def test_employee_list_tomorrow_schedule_and_sales(self):
        self.assertEqual(common_read_request("Muéstrame la lista de empleados"), ("listar_empleados", {}))
        self.assertEqual(common_read_request("Mi horario mañana"), ("consultar_horarios_empleados", {"desde": "2026-09-08", "hasta": "2026-09-08"}))
        self.assertEqual(common_read_request("Cuánto vendimos ayer"), ("consultar_ventas", {"desde": "2026-09-06", "hasta": "2026-09-06"}))

    def test_unrecognized_filters_or_writes_must_not_be_silently_ignored(self):
        for text in ("Paga 1000 en efectivo", "Muéstrame los pagos de hoy de William", "Cuánto pagué hoy en Daviplata", "Cuánto vendimos ayer en Yerbabuena", "Mi horario mañana y cancélalo", "Muestra los pagos mayores de 5000"):
            with self.subTest(text=text):
                self.assertIsNone(common_read_request(text))


class ConversationQueueTests(TestCase):
    def setUp(self):
        self.number = 80000
        self.client_stub = SimpleNamespace(send_message=MagicMock(), download_voice=MagicMock())
        for patcher in (
            patch.object(bot, "is_feature_enabled", return_value=True),
            patch.object(bot, "TelegramApiClient", return_value=self.client_stub),
            patch.object(bot, "logger"),
        ):
            patcher.start()
        self.addCleanup(patch.stopall)

    def message(self, chat=1, **kwargs):
        self.number += 1
        return TelegramActualizacion.objects.create(update_id=self.number, telegram_user_id=chat, telegram_chat_id=chat, chat_type="private", tipo=kwargs.pop("tipo", "TEXTO"), texto="Consulta", **kwargs)

    def test_one_chat_waits_while_another_can_progress(self):
        earlier = self.message(estado="PROCESANDO", iniciado_en=timezone.now())
        later = self.message()
        other = self.message(chat=2)
        with patch.object(bot, "build_reply", return_value=bot.BotReply("OK")) as build:
            self.assertTrue(bot.process_next_update())
            self.assertEqual(build.call_args.args[0].pk, other.pk)
            self.assertFalse(bot.process_next_update())
            earlier.estado = "PROCESADO"
            earlier.save(update_fields=["estado"])
            self.assertTrue(bot.process_next_update())
            self.assertEqual(build.call_args.args[0].pk, later.pk)

    def test_same_timestamp_uses_update_id_order(self):
        first, second = self.message(), self.message()
        TelegramActualizacion.objects.update(recibido_en=timezone.now())
        with patch.object(bot, "build_reply", return_value=bot.BotReply("OK")) as build:
            bot.process_next_update()
            self.assertEqual(build.call_args.args[0].pk, first.pk)
            bot.process_next_update()
            self.assertEqual(build.call_args.args[0].pk, second.pk)

    def test_exhausted_ai_is_not_immediately_reprocessed_and_error_has_one_period(self):
        message = self.message()
        with patch.object(bot, "build_reply", side_effect=bot.TelegramAIUnavailable("Límite de uso.")) as build:
            bot.process_next_update()
            self.assertFalse(bot.process_next_update())
        build.assert_called_once()
        message.refresh_from_db()
        self.assertEqual((message.estado, message.intentos), ("ERROR", 1))
        self.assertNotIn("..", self.client_stub.send_message.call_args.args[1])

    def test_cached_transcription_is_not_sent_to_speech_api_again(self):
        self.message(tipo="VOZ", transcripcion="Mi horario mañana")
        with patch.object(bot, "transcribe_voice") as transcribe, patch.object(bot, "build_reply", return_value=bot.BotReply("OK")):
            bot.process_next_update()
        transcribe.assert_not_called()
        self.client_stub.download_voice.assert_not_called()

    def test_stale_last_attempt_does_not_block_chat_forever(self):
        stale = self.message(estado="PROCESANDO", intentos=3, iniciado_en=timezone.now() - timedelta(minutes=11))
        following = self.message()
        bot.recover_stale_updates()
        stale.refresh_from_db()
        self.assertEqual(stale.estado, "ERROR")
        with patch.object(bot, "build_reply", return_value=bot.BotReply("OK")) as build:
            self.assertTrue(bot.process_next_update())
        self.assertEqual(build.call_args.args[0].pk, following.pk)

    def test_shortcut_is_available_when_all_ai_providers_are_down(self):
        user = Usuario.objects.create_user("Prueba atajos", rolid=Rol.objects.create(nombre="Web Master"))
        TelegramUsuario.objects.create(usuario=user, telegram_user_id=1, telegram_chat_id=1)
        message = self.message()
        message.texto = "Muéstrame los pagos de hoy"
        with patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No llamar IA")), patch.object(bot, "_execute_tool", return_value=bot.BotReply("Lista de pagos")) as execute:
            self.assertEqual(bot.build_reply(message, self.client_stub).text, "Lista de pagos")
        self.assertEqual(execute.call_args.args[1], "consultar_pagos")
