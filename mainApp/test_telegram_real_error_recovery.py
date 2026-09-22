"""Regresiones de audios transcritos: confirmación, contexto y fallos 400/413."""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from .services import telegram_ai_policy as policy, telegram_bot as bot
from .services.telegram_ai_output import AIOutputError, AIToolSelection, parse_selection
from .services.telegram_assistant import common_read_request
from .services.telegram_shortcuts import specific_read_request
from .test_telegram_ai_output import chat_calls, gemini_calls, response


def chat_json(value):
    return response({"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value, ensure_ascii=False)}}]})


class TranscribedShortcutTests(SimpleTestCase):
    def test_real_daily_payment_question_uses_local_read_for_audio_and_text(self):
        profile = SimpleNamespace(usuario=SimpleNamespace(is_active=True), save=MagicMock(), telegram_username="test", nombre_telegram="Test")
        questions = (
            "¿Cuánto se ha pagado el día de hoy?",
            "Hola, ¿me puedes decir cuánto se ha pagado el día de hoy?",
            "Muéstrame los pagos del día de hoy",  # Contracción: también debe reconocerse.
            "¿Cuánto se ha pagado el día de ayer?",
        )
        for question in questions:
            for voice in (False, True):
                update = SimpleNamespace(tipo="VOZ" if voice else "TEXTO", texto="" if voice else question,
                                         transcripcion=question if voice else "", telegram_chat_id=1,
                                         telegram_username="test", nombre_telegram="Test")
                with self.subTest(question=question, voice=voice), patch.object(bot, "_profile_for_update", return_value=profile), patch.object(bot, "_execute_tool", return_value=bot.BotReply("Total real")) as execute, patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No necesita IA")), patch("mainApp.services.telegram_assistant.timezone.localdate", return_value=date(2026, 9, 20)):
                    self.assertEqual(bot.build_reply(update, MagicMock()).text, "Total real")
                    self.assertEqual(execute.call_args.args[1], "consultar_pagos")
                    args = execute.call_args.args[2]
                    self.assertEqual(args["desde"], "2026-09-19" if "ayer" in question else "2026-09-20")
                    self.assertEqual(args["desde"], args["hasta"])
                    self.assertEqual(args["detalle"], question.startswith("Muéstrame"))
                    self.assertFalse(args["desglose_por_medio"])

    def test_date_equivalence_does_not_silently_remove_filters_or_rename_products(self):
        for text in ("Cuánto se ha pagado el día de hoy en Yerbabuena", "Cuánto se ha pagado el día de hoy y registra un pago", "Cuánto se ha pagado el día de hoy de William"):
            with self.subTest(text=text):
                self.assertIsNone(common_read_request(text))
                self.assertIsNone(specific_read_request(text))
        self.assertEqual(specific_read_request("Dime el precio de El día de hoy")[1]["consulta"], "dia de hoy")

    def test_spoken_confirmation_recovers_buttons_without_ai_or_action(self):
        profile = SimpleNamespace(usuario=SimpleNamespace(is_active=True), save=MagicMock(), telegram_username="test", nombre_telegram="Test")
        for text in ("Confirmar", "Confirmar.", "Confirmo", "Confirmar devolución"):
            for voice in (False, True):
                update = SimpleNamespace(tipo="VOZ" if voice else "TEXTO", texto="" if voice else text,
                                         transcripcion=text if voice else "", telegram_chat_id=1,
                                         telegram_username="test", nombre_telegram="Test")
                with self.subTest(text=text, voice=voice), patch.object(bot, "_profile_for_update", return_value=profile), patch.object(bot, "_execute_tool") as execute, patch.object(bot, "_intelligent_function_call") as intelligent, patch.object(bot, "show_pending_buttons", return_value=bot.BotReply("Propuesta para revisar")) as recover:
                    reply = bot.build_reply(update, MagicMock())
                    self.assertEqual(reply.text, "Propuesta para revisar")
                    recover.assert_called_once_with(profile, update)
                    execute.assert_not_called()
                    intelligent.assert_not_called()

    def test_purchase_routes_to_payments_without_altering_original(self):
        text = "Compra en Calixo por valor de $547,099."
        definitions, prompt, _ = bot._ai_request_context(text)
        names = {d["name"] for d in definitions}
        self.assertIn("preparar_registro_pago", names)
        self.assertNotIn("preparar_turno_empleado", names)
        self.assertLess(len(names), len(bot.TOOL_FUNCTIONS))
        self.assertIn("botón Confirmar", prompt)

    def test_short_payment_answer_uses_context_only_to_select_schemas(self):
        history = [{"role": "user", "text": "Compra en Calixo por valor de $547,099."},
                   {"role": "model", "text": "¿Con qué medio lo pagaste?"}]
        for text in ("Neki", "pornéki", "En efectivo", "Tarjeta."):
            with self.subTest(text=text):
                definitions, _, supplied_history = bot._ai_request_context(text, history)
                names = {d["name"] for d in definitions}
                self.assertIn("preparar_registro_pago", names)
                self.assertNotIn("preparar_turno_empleado", names)
                self.assertEqual(supplied_history, history)
                self.assertIsNone(common_read_request(text))
        self.assertEqual(policy.selected_tool_names("Neki", [], bot.TOOL_FUNCTIONS), set(bot.TOOL_FUNCTIONS))


@override_settings(GEMINI_API_KEY="fake-gemini", GROQ_API_KEY="fake-groq", CEREBRAS_API_KEY="", OPENROUTER_API_KEY="")
class ProviderRealErrorRecoveryTests(SimpleTestCase):
    def setUp(self):
        bot._AI_PROVIDER_FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)
        sleeper = patch.object(bot.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_native_tool_400_recovers_with_json_and_same_full_request(self):
        text = "¿Cuánto se ha pagado entre el 1 y el 20 de septiembre por medio de pago?"
        args = {"desde": "2026-09-01", "hasta": "2026-09-20", "desglose_por_medio": True}
        failures = [response({}, 503), response({"error": {"code": "tool_use_failed", "failed_generation": "PRIVATE"}}, 400)]
        with patch.object(bot.requests, "post", side_effect=[*failures, chat_json({"name": "consultar_pagos", "arguments": args})]) as post, patch.object(bot, "_execute_tool") as execute:
            self.assertEqual(bot._intelligent_function_call(text), ("consultar_pagos", args, ""))
        self.assertEqual(post.call_count, 3)
        initial, repaired = [call.kwargs["json"] for call in post.call_args_list[1:]]
        self.assertEqual(initial["messages"][1:], repaired["messages"][1:])
        self.assertEqual(repaired["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", repaired)
        self.assertNotIn("tool_choice", repaired)
        self.assertIn('"name":"consultar_pagos"', repaired["messages"][0]["content"])
        self.assertNotIn("PRIVATE", repaired["messages"][0]["content"])
        execute.assert_not_called()

    def test_json_repair_can_ask_missing_data_without_actions(self):
        with patch.object(bot.requests, "post", return_value=chat_json({"pregunta": "¿Con qué medio lo pagaste?"})), patch.object(bot, "_execute_tool") as execute:
            self.assertEqual(bot._groq_function_call("Registra un pago", repair="format"), ("", {}, "¿Con qué medio lo pagaste?"))
            execute.assert_not_called()

    def test_truncated_json_repair_retains_larger_output_budget(self):
        with patch.object(bot.requests, "post", return_value=chat_json({"name": "consultar_pagos", "arguments": {}})) as post:
            self.assertEqual(bot._groq_function_call("Consulta pagos", repair="truncated")[0], "consultar_pagos")
        self.assertEqual(post.call_args.kwargs["json"]["max_completion_tokens"], 8192)

    def test_json_repair_never_accepts_unknown_tools_extra_fields_or_guessed_money(self):
        for value in (
            {"name": "eliminar_pago", "arguments": {"id": 1}},
            {"name": "preparar_registro_pago", "arguments": {"concepto": "AGUA", "monto": "1.000", "medio_pago": "efectivo"}},
            {"name": "preparar_registro_pago", "arguments": {"concepto": "AGUA", "monto": 1000, "medio_pago": "efectivo", "sql": "DELETE"}},
            {"name": "consultar_pagos", "arguments": {}, "resultado": "Inventado"},
            "Ya guardé el pago", [], {"pregunta": "Ya guardé el pago"},
        ):
            with self.subTest(value=value), patch.object(bot.requests, "post", return_value=chat_json(value)), patch.object(bot, "_execute_tool") as execute:
                with self.assertRaises(bot.TelegramAIProviderError):
                    bot._groq_function_call("Registra un pago de agua", repair="format")
                execute.assert_not_called()

    def test_413_uses_catalog_then_full_selected_schema_preserving_conversation(self):
        text = "Neki"
        history = [{"role": "user", "text": "Son 547099 para CALIXO"},
                   {"role": "model", "text": "¿Cómo lo pagaste?"}]
        args = {"concepto": "CALIXO", "monto": 547099, "medio_pago": "nequi"}
        replies = [response({}, 503), response({}, 413), chat_json({"herramientas": ["preparar_registro_pago"]}), chat_calls(("preparar_registro_pago", args))]
        with patch.object(bot.requests, "post", side_effect=replies) as post, patch.object(bot, "_execute_tool") as execute:
            self.assertEqual(bot._intelligent_function_call(text, history), ("preparar_registro_pago", args, ""))
        self.assertEqual(post.call_count, 4)
        original, selector, final = [call.kwargs["json"] for call in post.call_args_list[1:]]
        self.assertEqual(original["messages"][1:], selector["messages"][1:])
        self.assertEqual(original["messages"][1:], final["messages"][1:])
        self.assertNotIn("tools", selector)
        self.assertEqual(selector["response_format"], {"type": "json_object"})
        expected = next(item for item in bot.GROQ_CHAT_TOOLS if item["function"]["name"] == "preparar_registro_pago")
        self.assertEqual(final["tools"], [expected])
        self.assertIn("botón Confirmar", final["messages"][0]["content"])
        self.assertLess(len(json.dumps(selector)), len(json.dumps(original)) * 0.5)
        self.assertLess(len(json.dumps(final)), len(json.dumps(original)) * 0.5)
        self.assertNotIn("Groq", bot._AI_PROVIDER_FAILURES)
        execute.assert_not_called()

    def test_selector_can_request_clarification_instead_of_guessing_tools(self):
        replies = [response({}, 503), response({}, 413), chat_json({"pregunta": "¿Qué información quieres consultar?"})]
        with patch.object(bot.requests, "post", side_effect=replies) as post:
            self.assertEqual(bot._intelligent_function_call("Lo que hablamos"), ("", {}, "¿Qué información quieres consultar?"))
        self.assertEqual(post.call_count, 3)

    def test_two_selected_read_tools_allow_validated_multi_query(self):
        reads = (("consultar_ventas", {"desde": "2026-09-01", "hasta": "2026-09-15"}),
                 ("consultar_pagos", {"desde": "2026-09-01", "hasta": "2026-09-15", "medio_pago": "efectivo"}))
        replies = [response({}, 503), response({}, 413), chat_json({"herramientas": [name for name, _ in reads]}), chat_calls(*reads)]
        with patch.object(bot.requests, "post", side_effect=replies) as post:
            name, args, _ = bot._intelligent_function_call("Ventas y pagos en efectivo del 1 al 15 de septiembre")
        self.assertEqual(name, "consultar_varias")
        self.assertEqual([json.loads(q["argumentos_json"]) for q in args["consultas"]], [a for _, a in reads])
        self.assertEqual({t["function"]["name"] for t in post.call_args.kwargs["json"]["tools"]}, {"consultar_ventas", "consultar_pagos", "consultar_varias"})

    def test_selected_schema_still_rejects_unselected_function(self):
        replies = [response({}, 503), response({}, 413), chat_json({"herramientas": ["consultar_pagos"]}), chat_calls(("preparar_registro_pago", {"concepto": "AGUA", "monto": 1000, "medio_pago": "efectivo"}))]
        with patch.object(bot.requests, "post", side_effect=replies) as post, patch.object(bot, "_execute_tool") as execute:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("Muéstrame los pagos")
        self.assertEqual(post.call_count, 4)
        execute.assert_not_called()

    def test_selector_requires_exact_known_unique_names_without_arguments(self):
        self.assertEqual(parse_selection('{"herramientas":["consultar_pagos"]}', bot.TOOL_FUNCTIONS), AIToolSelection(("consultar_pagos",)))
        for value in (
            {"herramientas": []}, {"herramientas": ["borrar_pago"]}, {"herramientas": ["consultar_varias"]},
            {"herramientas": ["consultar_pagos", "consultar_pagos"]},
            {"herramientas": ["consultar_pagos"], "arguments": {}},
            {"herramientas": ["consultar_pagos", "consultar_ventas", "consultar_balance", "buscar_producto", "consultar_turnos"]},
            {"herramientas": [{}]}, {"name": "consultar_pagos", "arguments": {}},
        ):
            with self.subTest(value=value), self.assertRaises(AIOutputError):
                parse_selection(json.dumps(value), bot.TOOL_FUNCTIONS)

    @override_settings(CEREBRAS_API_KEY="fake-c", OPENROUTER_API_KEY="fake-o")
    def test_four_failed_providers_cannot_start_two_phase_repair_beyond_cap(self):
        replies = [response({}, 401), response({}, 413), response({}, 401), response({}, 401)]
        with patch.object(bot.requests, "post", side_effect=replies) as post:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("Neki")
        self.assertEqual(post.call_count, 4)
        self.sleep.assert_not_called()

    @override_settings(CEREBRAS_API_KEY="fake-c")
    def test_three_providers_allow_both_recovery_stages_within_five_attempts(self):
        replies = [response({}, 503), response({}, 413), response({}, 401),
                   chat_json({"herramientas": ["consultar_pagos"]}), chat_calls(("consultar_pagos", {}))]
        with patch.object(bot.requests, "post", side_effect=replies) as post:
            self.assertEqual(bot._intelligent_function_call("Consulta pagos"), ("consultar_pagos", {}, ""))
        self.assertEqual(post.call_count, 5)

    def test_slow_selector_cannot_start_interpretation_past_retry_budget(self):
        now = [100]
        replies = iter([response({}, 503), response({}, 413), chat_json({"herramientas": ["consultar_pagos"]})])

        def slow(*args, **kwargs):
            result = next(replies)
            if kwargs["json"].get("response_format"):
                now[0] += 36
            return result

        with patch.object(bot.time, "monotonic", side_effect=lambda: now[0]), patch.object(bot.requests, "post", side_effect=slow) as post:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("Consulta pagos")
        self.assertEqual(post.call_count, 3)

    def test_413_and_tool_400_respect_explicit_retry_after(self):
        for status, body in ((413, {}), (400, {"error": {"code": "tool_use_failed"}})):
            bot._AI_PROVIDER_FAILURES.clear()
            with self.subTest(status=status), patch.object(bot.requests, "post", side_effect=[response({}, 401), response(body, status, {"Retry-After": "60"})]) as post:
                with self.assertRaises(bot.TelegramAIUnavailable):
                    bot._intelligent_function_call("Consulta pagos")
                self.assertEqual(post.call_count, 2)
                self.assertEqual(bot._AI_PROVIDER_FAILURES["Groq"][2].delay, 60)
        self.sleep.assert_not_called()

    def test_successful_first_attempt_does_not_add_repair_requests(self):
        with patch.object(bot.requests, "post", return_value=gemini_calls(("consultar_pagos", {}))) as post:
            self.assertEqual(bot._intelligent_function_call("Consulta pagos")[0], "consultar_pagos")
        post.assert_called_once()
        self.sleep.assert_not_called()

    def test_authentication_error_in_413_is_never_retried_as_context_size(self):
        with override_settings(GEMINI_API_KEY=""), patch.object(bot.requests, "post", return_value=response({"error": {"code": "invalid_api_key"}}, 413)) as post:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("Consulta pagos")
        post.assert_called_once()
        self.assertEqual(bot._AI_PROVIDER_FAILURES["Groq"][2].kind, "authentication")
        self.sleep.assert_not_called()

    def test_logs_never_contain_provider_body_or_original_question(self):
        replies = [response({}, 503), response({"error": {"message": "PRIVATE_SECRET", "failed_generation": "PRIVATE"}}, 413), chat_json({"herramientas": ["unknown"]})]
        with patch.object(bot.requests, "post", side_effect=replies), self.assertLogs(bot.logger, level="INFO") as logged:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("PRIVATE BUSINESS QUESTION")
        self.assertNotIn("PRIVATE", str(logged.output))
        self.assertNotIn("fake-groq", str(logged.output))
