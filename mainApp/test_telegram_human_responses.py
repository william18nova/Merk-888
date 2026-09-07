from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from mainApp.models import (
    ConceptoEgreso, Egreso, MetodoPago, Rol, TelegramAccionPendiente,
    TelegramActualizacion, TelegramUsuario, Usuario,
)
from mainApp.services import telegram_bot as bot
from mainApp.services.telegram_wording import CONVERSATION_STYLE, period_phrase, social_reply


class HumanWordingTests(SimpleTestCase):
    def test_relative_dates_keep_the_exact_date(self):
        today = date(2026, 9, 7)
        self.assertEqual(period_phrase(today, today, today), "hoy (07/09/2026)")
        yesterday = today - timedelta(days=1)
        self.assertEqual(period_phrase(yesterday, yesterday, today), "ayer (06/09/2026)")
        self.assertEqual(period_phrase(date(2026, 8, 1), yesterday, today), "del 01/08/2026 al 06/09/2026")
        self.assertEqual(period_phrase(date(2026, 8, 1), date(2026, 8, 1), today), "el 01/08/2026")

    def test_greetings_do_not_swallow_requests_or_confirm_changes(self):
        for text in ("Hola", "¡Buenos días!", "Muchas gracias", "Chao"):
            with self.subTest(text=text):
                self.assertTrue(social_reply(text))
        for text in ("Hola, muestra la venta 123", "Gracias y registra 5000 de agua", "sí", "confirmar", "/pagos"):
            with self.subTest(text=text):
                self.assertIsNone(social_reply(text))

    def test_human_tone_is_present_in_full_and_reduced_prompts(self):
        self.assertIn(CONVERSATION_STYLE, bot._assistant_system_prompt())
        for text in ("Analiza los pagos por concepto", "Cambia el horario de Camila", "Ayúdame con algo distinto"):
            with self.subTest(text=text):
                _, prompt, _ = bot._ai_request_context(text)
                self.assertIn(CONVERSATION_STYLE, prompt)
                self.assertIn("botón Confirmar", prompt)
                self.assertIn("no inventes", prompt)

    def test_provider_details_are_not_sent_in_error_messages(self):
        update = SimpleNamespace(tipo="TEXTO", transcripcion="")
        for error in (
            bot.TelegramAIUnavailable("Gemini HTTP 429; token=FAKE_SECRET; Retry-After=300"),
            bot.TelegramAIProviderError("Groq", "authentication", status=401),
            bot.TelegramConfigurationError("Falta GEMINI_API_KEY=FAKE_SECRET"),
            bot.TelegramExternalError("HTTP 503; endpoint privado"),
            DatabaseError("SELECT * FROM secret_table; FAKE_SECRET"),
        ):
            with self.subTest(error=type(error).__name__):
                message = bot._user_error_message(error, update)
                for technical in ("Gemini", "Groq", "HTTP", "FAKE_SECRET", "Retry-After", "SELECT", "endpoint", "API_KEY"):
                    self.assertNotIn(technical, message)
                self.assertNotIn("No se guardó nada", message)

    def test_audio_failure_offers_text_without_claiming_a_payment_was_saved(self):
        update = SimpleNamespace(tipo="VOZ", transcripcion="")
        text = bot._user_error_message(bot.TelegramExternalError("HTTP 503"), update)
        self.assertIn("por escrito", text)
        update.transcripcion = "Registra un pago"
        text = bot._user_error_message(bot.TelegramExternalError("HTTP 503"), update)
        self.assertIn("revisa si quedó registrado", text)

    def test_validation_questions_stay_useful_and_keep_punctuation(self):
        update = SimpleNamespace(tipo="TEXTO", transcripcion="")
        question = "¿Cuánto pagaste y con qué medio?"
        self.assertEqual(bot._user_error_message(bot.TelegramBotError(question), update), question)
        self.assertEqual(bot._user_error_message(bot.TelegramBotError("El monto debe ser mayor que cero"), update), "El monto debe ser mayor que cero.")
        text = bot._user_error_message(PermissionDenied("registrar_egreso"), update)
        self.assertIn("no tiene permiso", text)
        self.assertNotIn("registrar_egreso", text)

    @override_settings(GROQ_API_KEY="fake-transcription-key")
    def test_raw_transcription_error_body_is_not_repeated(self):
        response = SimpleNamespace(status_code=400, ok=False, json=lambda: {"error": {"message": "PRIVATE_PROVIDER_BODY"}})
        with patch.object(bot.requests, "post", return_value=response):
            with self.assertRaises(bot.TelegramConfigurationError) as raised:
                bot.transcribe_voice(b"fake-audio")
        self.assertNotIn("PRIVATE_PROVIDER_BODY", str(raised.exception))
        self.assertIn("HTTP 400", str(raised.exception))


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True)
class HumanBusinessRepliesTests(TestCase):
    def setUp(self):
        MetodoPago.objects.update_or_create(codigo="efectivo", defaults={"nombre": "Efectivo", "activo": True, "es_efectivo": True, "es_sistema": True, "orden": 10})
        self.user = Usuario.objects.create_user("Prueba tono cercano", rolid=Rol.objects.create(nombre="Web Master"))
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=9821, telegram_chat_id=9821)
        self.concept = ConceptoEgreso.objects.create(nombre="COCA-COLA")
        self.expense = Egreso.objects.create(concepto=self.concept, monto=Decimal("1000.25"), medio_pago="efectivo", registrado_por_nombre="William")
        self.client_stub = SimpleNamespace(send_message=MagicMock(), answer_callback=MagicMock())
        self.sequence = 98500

    def message(self, text, **kwargs):
        self.sequence += 1
        return TelegramActualizacion.objects.create(
            update_id=self.sequence, telegram_user_id=9821, telegram_chat_id=9821,
            chat_type="private", tipo=kwargs.pop("tipo", "TEXTO"), texto=text, **kwargs,
        )

    def test_simple_total_is_natural_exact_and_not_attributed_to_the_wrong_user(self):
        reply = bot._execute_tool(self.profile, "consultar_pagos", {})
        self.assertEqual(reply.text, f"Hoy ({timezone.localdate():%d/%m/%Y}) se han pagado $1.000,25.")
        self.assertNotIn("has pagado", reply.text)
        self.assertNotIn("\n", reply.text)
        self.assertIsNone(reply.reply_markup)

    def test_empty_search_keeps_requested_date_and_filters(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        text = bot._execute_tool(self.profile, "consultar_pagos", {
            "desde": tomorrow.isoformat(), "hasta": tomorrow.isoformat(), "concepto": "AGUA", "medio_pago": "nequi",
        }).text
        for part in ("No encontré pagos registrados", tomorrow.strftime("%d/%m/%Y"), "AGUA", "Nequi", "$0"):
            self.assertIn(part, text)

    def test_lists_keep_ids_money_method_and_user_without_extra_ai(self):
        with patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No llamar IA para embellecer")):
            reply = bot.build_reply(self.message("Muéstrame los pagos de hoy"), self.client_stub)
        for part in (f"#{self.expense.pk}", "$1.000,25", "COCA-COLA", "Efectivo", "Registró: William", "1 pago"):
            self.assertIn(part, reply.text)
        self.assertNotIn("Así se reparten", reply.text)
        self.assertEqual(Egreso.objects.count(), 1)

    def test_short_social_messages_do_not_consume_ai_or_create_proposals(self):
        with patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No llamar IA para saludar")):
            for text in ("Hola Jarvis", "gracias", "buenos días"):
                self.assertEqual(bot.build_reply(self.message(text), self.client_stub).intent, "conversacion")
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_greeting_with_request_still_uses_the_normal_flow(self):
        with patch.object(bot, "_intelligent_function_call", return_value=("consultar_pagos", {}, "")) as ai:
            reply = bot.build_reply(self.message("Hola, cuánto pagamos esta quincena"), self.client_stub)
        ai.assert_called_once()
        self.assertIn("se han pagado", reply.text)

    def test_natural_confirmation_preserves_cents_and_does_not_save_early(self):
        reply = bot.tool_prepare_expense(self.profile, {"concepto": "ARRIENDO", "monto": 20000.75, "medio_pago": "efectivo"})
        self.assertIn("¿Confirmas que registre este pago?", reply.text)
        self.assertIn("$20.000,75", reply.text)
        self.assertIn("Todavía no lo he guardado", reply.text)
        self.assertEqual(Egreso.objects.count(), 1)
        pending = TelegramAccionPendiente.objects.get()
        callback = SimpleNamespace(texto=f"confirm:{pending.pk}", callback_query_id="human-confirm")
        saved = bot._handle_callback(callback, self.profile, self.client_stub)
        self.assertIn("el pago quedó registrado", saved.text)
        self.assertIn("ARRIENDO por $20.000,75 en Efectivo", saved.text)
        self.assertEqual(Egreso.objects.get(concepto__nombre="ARRIENDO").monto, Decimal("20000.75"))
        bot._handle_callback(callback, self.profile, self.client_stub)
        self.assertEqual(Egreso.objects.count(), 2)

    def test_similar_name_selection_keeps_exact_amount_and_confirmation(self):
        reply = bot.tool_prepare_expense(self.profile, {"concepto": "cocacola", "monto": 1.25, "medio_pago": "efectivo"})
        self.assertIn("¿Usamos uno de estos o creamos COCACOLA?", reply.text)
        pending = TelegramAccionPendiente.objects.get()
        index = next(i for i, option in enumerate(pending.argumentos["concepto_opciones"]) if option["id"] == self.concept.pk)
        selected = bot._select_expense_concept(pending, "concept", str(index))
        self.assertIn("COCA-COLA por $1,25", selected.text)
        self.assertIn("Todavía no lo he guardado", selected.text)
        self.assertEqual(Egreso.objects.count(), 1)

    def test_friendly_error_keeps_technical_diagnostic_only_in_audit(self):
        update = self.message("Haz una consulta compleja")
        detail = "Gemini HTTP 429 y Groq HTTP 503; diagnóstico de prueba"
        with patch.object(bot, "is_feature_enabled", return_value=True), patch.object(bot, "TelegramApiClient", return_value=self.client_stub), patch.object(bot, "logger"), patch.object(bot, "build_reply", side_effect=bot.TelegramAIUnavailable(detail)):
            self.assertTrue(bot.process_next_update())
        update.refresh_from_db()
        self.assertEqual(update.error, detail)
        self.assertEqual(update.estado, "ERROR")
        sent = self.client_stub.send_message.call_args.args[1]
        self.assertIn("Ahora mismo no pude atender", sent)
        self.assertNotIn("HTTP", sent)
        self.assertNotIn("Gemini", sent)
