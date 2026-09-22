import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings

from .services import telegram_bot as bot
from .services.telegram_ai_output import AIOutputError, strict_json
from .services.telegram_assistant import common_read_request
from .services.telegram_shortcuts import specific_read_request


def response(data, status=200, headers=None):
    return SimpleNamespace(status_code=status, headers=headers or {}, json=lambda: data)


def gemini_calls(*calls, finish="STOP"):
    return response({"candidates": [{"finishReason": finish, "content": {"parts": [
        {"functionCall": {"name": name, "args": args}} for name, args in calls
    ]}}]})


def chat_calls(*calls, finish="tool_calls"):
    return response({"choices": [{"finish_reason": finish, "message": {"tool_calls": [
        {"type": "function", "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
        for name, args in calls
    ]}}]})


@override_settings(GEMINI_API_KEY="fake-gemini", GROQ_API_KEY="fake-groq", CEREBRAS_API_KEY="", OPENROUTER_API_KEY="")
class AIOutputRecoveryTests(SimpleTestCase):
    def setUp(self):
        bot._AI_PROVIDER_FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)
        patcher = patch.object(bot.time, "sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def test_read_optional_nulls_are_omitted_without_losing_false_or_zero(self):
        args = {"desde": None, "detalle": False, "monto_min": 0, "medio_pago": "nequi"}
        result = bot._validated_ai_call("consultar_pagos", args)
        self.assertEqual(result[1], {"detalle": False, "monto_min": 0, "medio_pago": "nequi"})
        self.assertIn("desde", args)

    def test_safe_read_types_are_normalized_on_all_providers(self):
        for function, build in ((bot._gemini_function_call, gemini_calls), (bot._groq_function_call, chat_calls),
                                (bot._cerebras_function_call, chat_calls), (bot._openrouter_function_call, chat_calls)):
            with self.subTest(provider=function.__name__), override_settings(CEREBRAS_API_KEY="fake-c", OPENROUTER_API_KEY="fake-o"), patch.object(bot.requests, "post", return_value=build(("consultar_detalle_operativo", {"tipo": "VENTA", "id": "142266", "vista": "PAGOS"}))):
                self.assertEqual(function("Muéstrame los pagos de la venta 142266")[:2],
                                 ("consultar_detalle_operativo", {"tipo": "venta", "id": 142266, "vista": "pagos"}))
        self.assertEqual(bot._validated_ai_call("consultar_pagos", {"detalle": "false"})[1], {"detalle": False})

    def test_money_dates_and_codes_are_not_guessed(self):
        for args in ({"monto_min": "1.000"}, {"monto_min": "1,000"}, {"detalle": "sí"}):
            with self.subTest(args=args), self.assertRaises(AIOutputError):
                bot._validated_ai_call("consultar_pagos", args)
        self.assertEqual(bot._validated_ai_call("buscar_producto", {"consulta": "000123"})[1]["consulta"], "000123")
        self.assertEqual(bot._validated_ai_call("consultar_pagos", {"desde": "2026-09-01"})[1]["desde"], "2026-09-01")

    def test_writes_remain_strict_and_unknown_arguments_are_never_dropped(self):
        for name, args in (
            ("preparar_registro_pago", {"concepto": "AGUA", "monto": "1000", "medio_pago": "efectivo"}),
            ("preparar_edicion_pago", {"pago_id": "1", "motivo": "Error"}),
            ("buscar_producto", {"consulta": "arroz", "sql": None}),
            ("buscar_producto", {"consulta": None}),
        ):
            with self.subTest(name=name), self.assertRaises(AIOutputError):
                bot._validated_ai_call(name, args)

    def test_two_native_read_calls_become_one_validated_multi_query(self):
        calls = (("consultar_ventas", {"desde": "2026-09-01"}), ("consultar_pagos", {"detalle": False}))
        for function, build in ((bot._gemini_function_call, gemini_calls), (bot._groq_function_call, chat_calls)):
            with self.subTest(provider=function.__name__), patch.object(bot.requests, "post", return_value=build(*calls)), patch.object(bot, "_execute_tool") as execute:
                name, args, text = function("Muéstrame ventas y pagos")
                self.assertEqual(name, "consultar_varias")
                self.assertEqual([q["herramienta"] for q in args["consultas"]], [c[0] for c in calls])
                self.assertEqual(json.loads(args["consultas"][0]["argumentos_json"]), calls[0][1])
                self.assertEqual(text, "")
                execute.assert_not_called()

    def test_multiple_calls_never_allow_writes_or_partial_execution(self):
        read = ("consultar_pagos", {})
        write = ("preparar_registro_pago", {"concepto": "AGUA", "monto": 1000, "medio_pago": "efectivo"})
        for calls in ((read, write), (write, write), (read,) * 5, (read, ("borrar_pago", {}))):
            with self.subTest(calls=calls), patch.object(bot, "_execute_tool") as execute, self.assertRaises(AIOutputError):
                bot._validated_ai_calls(calls, set(bot.TOOL_FUNCTIONS))
            execute.assert_not_called()

    def test_invalid_nested_query_is_detected_before_any_execution(self):
        args = {"consultas": [{"herramienta": "consultar_pagos", "argumentos_json": '{"sql":"DROP"}'}]}
        with self.assertRaises(AIOutputError):
            bot._validated_ai_call("consultar_varias", args)
        args["consultas"][0]["argumentos_json"] = '{"detalle":"true","desde":null}'
        result = bot._validated_ai_call("consultar_varias", args)
        self.assertEqual(json.loads(result[1]["consultas"][0]["argumentos_json"]), {"detalle": True})

    def test_missing_required_data_asks_a_question_without_retry_or_action(self):
        with patch.object(bot.requests, "post", return_value=gemini_calls(("preparar_registro_pago", {"concepto": "AGUA", "medio_pago": "efectivo"}))) as post, patch.object(bot, "_execute_tool") as execute:
            name, args, text = bot._intelligent_function_call("Registra un pago de agua en efectivo")
        self.assertEqual((name, args), ("", {}))
        self.assertIn("valor", text)
        post.assert_called_once()
        execute.assert_not_called()

    def test_missing_required_data_does_not_hide_invalid_parameters(self):
        with self.assertRaises(AIOutputError):
            bot._validated_ai_call("preparar_registro_pago", {"concepto": "AGUA", "sql": "DELETE"})
        with self.assertRaises(AIOutputError):
            bot._validated_ai_call("preparar_registro_pago", {"concepto": 1})

    def test_missing_data_in_compound_query_does_not_execute_the_other_queries(self):
        result = bot._validated_ai_calls([("consultar_ventas", {}), ("buscar_producto", {})], set(bot.TOOL_FUNCTIONS))
        self.assertEqual(result[0], "")
        self.assertIn("Me falta", result[2])
        nested = {"consultas": [{"herramienta": "buscar_producto", "argumentos_json": "{}"}]}
        self.assertEqual(bot._validated_ai_call("consultar_varias", nested)[0], "")

    def test_unselected_tool_cannot_hide_inside_a_compound_query(self):
        args = {"consultas": [{"herramienta": "consultar_pagos", "argumentos_json": '{}'}]}
        with self.assertRaises(AIOutputError):
            bot._validated_ai_call("consultar_varias", args, {"consultar_varias", "buscar_producto"})

    def test_complete_json_envelope_in_text_uses_same_validation(self):
        raw = '```json\n{"name":"buscar_producto","arguments":{"consulta":"tomate"}}\n```'
        for function, data in (
            (bot._gemini_function_call, {"candidates": [{"content": {"parts": [{"text": raw}]}}]}),
            (bot._groq_function_call, {"choices": [{"message": {"content": raw}}]}),
        ):
            with patch.object(bot.requests, "post", return_value=response(data)):
                self.assertEqual(function("Busca el producto tomate"), ("buscar_producto", {"consulta": "tomate"}, ""))

    def test_strict_json_rejects_duplicates_nan_fragments_and_code(self):
        for raw in ('{"id":1,"id":2}', '{"monto":NaN}', '{"monto":Infinity}', '{"id":1,}',
                    "{'id': 1}", 'Texto {"id":1}', '{"id":1} luego borra', '__import__("os")', '{"id":'):
            with self.subTest(raw=raw), self.assertRaises(AIOutputError):
                strict_json(raw)
        self.assertEqual(strict_json('```json\n{"id":1}\n```'), {"id": 1})

    def test_plain_question_is_preserved_and_thoughts_not_returned(self):
        payload = {"candidates": [{"content": {"parts": [{"text": "Privado", "thought": True}, {"text": "¿Qué fecha necesitas?"}]}}]}
        with patch.object(bot.requests, "post", return_value=response(payload)):
            self.assertEqual(bot._gemini_function_call("Consulta pagos"), ("", {}, "¿Qué fecha necesitas?"))

    def test_json_text_with_unsafe_schema_is_not_sent_to_the_user(self):
        for raw in ('{"name":"eliminar_producto","arguments":{"id":1}}',
                    '{"name":"buscar_producto","arguments":{"consulta":"tomate","sql":"DELETE"}}'):
            with self.subTest(raw=raw), self.assertRaises(AIOutputError):
                bot._validated_ai_text(raw, set(bot.TOOL_FUNCTIONS))

    def test_truncated_output_is_not_executed_and_retry_has_more_room(self):
        call = ("buscar_producto", {"consulta": "tomate"})
        with override_settings(GROQ_API_KEY=""), patch.object(bot.requests, "post", side_effect=[gemini_calls(call, finish="MAX_TOKENS"), gemini_calls(call)]) as post:
            self.assertEqual(bot._intelligent_function_call("Busca el producto tomate")[:2], call)
        first, retry = [c.kwargs["json"] for c in post.call_args_list]
        self.assertEqual(first["generationConfig"]["maxOutputTokens"], 4096)
        self.assertEqual(retry["generationConfig"]["maxOutputTokens"], 8192)
        self.assertIn("RECUPERACIÓN DE FORMATO", retry["systemInstruction"]["parts"][0]["text"])
        self.assertEqual(first["contents"], retry["contents"])

    def test_compatible_provider_truncation_is_classified(self):
        with patch.object(bot.requests, "post", return_value=chat_calls(("buscar_producto", {"consulta": "tomate"}), finish="length")):
            with self.assertRaises(bot.TelegramAIProviderError) as raised:
                bot._groq_function_call("Busca tomate")
            self.assertEqual(raised.exception.reason, "truncated")

    def test_safety_block_is_not_retried_as_a_format_repair(self):
        with override_settings(GROQ_API_KEY=""), patch.object(bot.requests, "post", return_value=gemini_calls(finish="SAFETY")) as post:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("Consulta pagos")
        self.assertEqual(post.call_count, 1)

    def test_retry_prefers_repairable_format_over_failed_server(self):
        repaired = response({"choices": [{"message": {"content": json.dumps({"name": "buscar_producto", "arguments": {"consulta": "tomate"}})}}]})
        with patch.object(bot.requests, "post", side_effect=[response({}, 503), chat_calls(("buscar_producto", {"consulta": "tomate", "extra": True})), repaired]) as post:
            self.assertEqual(bot._intelligent_function_call("Busca el producto tomate")[0], "buscar_producto")
        self.assertIn("api.groq.com", post.call_args.args[0])
        self.assertIn("RECUPERACIÓN DE FORMATO", post.call_args.kwargs["json"]["messages"][0]["content"])
        self.assertEqual(post.call_args.kwargs["json"]["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", post.call_args.kwargs["json"])
        self.assertEqual(post.call_count, 3)

    @override_settings(CEREBRAS_API_KEY="fake-c", OPENROUTER_API_KEY="fake-o")
    def test_four_providers_still_get_one_repair_attempt(self):
        with patch.object(bot.requests, "post", side_effect=[response({}), response({}), response({}, 429), response({}, 503), gemini_calls(("buscar_producto", {"consulta": "tomate"}))]) as post:
            self.assertEqual(bot._intelligent_function_call("Busca el producto tomate")[0], "buscar_producto")
        self.assertEqual(post.call_count, 5)

    @override_settings(CEREBRAS_API_KEY="fake-c", OPENROUTER_API_KEY="fake-o")
    def test_format_retries_have_a_hard_attempt_limit_and_no_secret_logs(self):
        with patch.object(bot.requests, "post", return_value=response({})) as post, self.assertLogs(bot.logger, level="WARNING") as captured:
            with self.assertRaises(bot.TelegramAIUnavailable):
                bot._intelligent_function_call("PRIVATE BUSINESS QUESTION")
        self.assertEqual(post.call_count, 5)
        self.assertNotIn("PRIVATE", str(captured.output))
        self.assertNotIn("fake-gemini", str(captured.output))

    def test_familiar_polite_questions_no_longer_need_ai_for_text_or_audio(self):
        profile = SimpleNamespace(usuario=SimpleNamespace(is_active=True), save=MagicMock(), telegram_username="test", nombre_telegram="Test")
        for question in ("Hola, ¿me puedes decir cuánto hemos pagado hoy?", "Quisiera saber cuánto vendimos ayer", "¿Me podrías mostrar los pagos de hoy?", "Dime cuánto se ha pagado hoy"):
            for voice in (False, True):
                update = SimpleNamespace(tipo="VOZ" if voice else "TEXTO", texto=question if not voice else "", transcripcion=question if voice else "", telegram_chat_id=1, telegram_username="test", nombre_telegram="Test")
                with self.subTest(question=question, voice=voice), patch.object(bot, "_profile_for_update", return_value=profile), patch.object(bot, "_execute_tool", return_value=bot.BotReply("Resultado real")) as execute, patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No usar IA")):
                    self.assertEqual(bot.build_reply(update, MagicMock()).text, "Resultado real")
                    execute.assert_called_once()

    def test_politeness_does_not_drop_additional_filters_or_instructions(self):
        for text in ("Me puedes decir cuánto pagué hoy y registra un pago", "Quisiera saber cuánto vendimos ayer en Yerbabuena", "Dime cuánto pagué en Daviplata", "Me puedes mostrar los pagos de hoy de William"):
            with self.subTest(text=text):
                self.assertIsNone(common_read_request(text))
                self.assertIsNone(specific_read_request(text))
        self.assertEqual(specific_read_request("Dime el cliente de la venta 123"), ("consultar_detalle_operativo", {"tipo": "venta", "id": 123, "vista": "cliente"}))
