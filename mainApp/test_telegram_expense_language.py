"""Lenguaje libre de pagos: proveedores simulados y base de pruebas."""
import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .models import Egreso, MetodoPago, Rol, TelegramAccionPendiente, TelegramActualizacion, TelegramAuditoria, TelegramUsuario, Usuario
from .services import telegram_bot as bot
from .services.telegram_ai_output import AIOutputError, validate_plain_reply
from .services.telegram_expense_language import PaymentContext, extract_payment, parse_payment_amount, payment_context
from .services.telegram_expense_shortcuts import expense_request
from .test_telegram_ai_output import gemini_calls, response


class PaymentEvidenceTests(SimpleTestCase):
    def test_cop_in_words_and_figures_are_exact(self):
        for text, value in (
            ("ochenta y cinco mil novecientos pesos", "85900"),
            ("veintidós mil", "22000"),
            ("un millón doscientos cincuenta mil", "1250000"),
            ("dos millones cien mil", "2100000"),
            ("2 millones", "2000000"),
            ("$85.900,50", "85900.50"),
        ):
            with self.subTest(text=text):
                self.assertEqual(parse_payment_amount(text), Decimal(value))
        for text in ("85,900", "0", "un millón y medio", "cien uno", "90 dólares", "-50000", "mil mil", "85.90"):
            with self.subTest(text=text):
                self.assertIsNone(parse_payment_amount(text))

    def test_evidence_checks_full_amount_and_literal_method_not_the_history(self):
        fields = {"concepto": "Taller Sol", "medio_texto": "Nequi"}
        for message, candidate in (
            ("Al Taller Sol le pasé 85 mil por Nequi, anótalo.", "85"),
            ("Al Taller Sol le pasé ochenta y cinco mil por Nequi, anótalo.", "cinco mil"),
            ("Al Taller Sol le pasé ochenta y cinco mil por Nequi, anótalo.", "ochenta"),
            ("Al Taller Sol le pasé 85,900 por Nequi, anótalo.", "85,900"),
            ("Al Taller Sol le pasé 85.900 por Nequi, anótalo.", "85900"),
            ("Al Taller Sol le pasé 85.900 y 25.000 por Nequi, anótalo.", "85.900"),
            ("Al Taller Sol le pasé cincuenta mil y treinta mil por Nequi, anótalo.", "treinta mil"),
            ("Al Taller Sol le pasé 85.900 dólares por Nequi, anótalo.", "85.900"),
        ):
            with self.subTest(message=message, candidate=candidate):
                result = extract_payment(dict(fields, monto_texto=candidate), PaymentContext(message))
                self.assertNotIn("monto", result.arguments)
        result = extract_payment(dict(fields, monto_texto="85.900", medio_texto="efectivo"), PaymentContext(
            "Al Taller Sol le pasé 85.900 por Nequi, anótalo."
        ))
        self.assertEqual(result.arguments, {"concepto": "Taller Sol", "monto": "85900"})

    def test_no_proposal_from_transfer_negation_historical_date_or_multiple_tasks(self):
        args = {"concepto": "Taller Sol", "monto_texto": "85.900", "medio_texto": "Nequi"}
        for message in (
            "Paga 85.900 al Taller Sol por Nequi",
            "No me registres el pago al Taller Sol de 85.900 por Nequi",
            "Al Taller Sol le pasé 85.900 por Nequi ayer",
            "Anota dos pagos al Taller Sol por 85.900 con Nequi",
            "Pago de 85.900 por Nequi a Taller Sol y dime cuánto vendimos",
        ):
            with self.subTest(message=message):
                self.assertEqual(expense_request(message), (None, False))
                result = extract_payment(args, PaymentContext(message))
                self.assertTrue(result.question)
                self.assertFalse(result.arguments)

    def test_rejects_fabricated_action_and_untyped_model_output(self):
        with self.assertRaises(AIOutputError):
            validate_plain_reply("Listo, ya registré tu pago.")
        with self.assertRaises(AIOutputError):
            extract_payment({"concepto": "Taller Sol", "monto": 85900}, PaymentContext("Pagué al Taller Sol"))

    def test_amount_correction_cannot_be_ignored_when_answering_for_missing_method(self):
        context = PaymentContext("Fue por Nequi, y en realidad fueron 50.000.", {"monto": "85000", "concepto": "Taller Sol"})
        result = extract_payment({"medio_texto": "Nequi"}, context)
        self.assertEqual(result.arguments, {"concepto": "Taller Sol", "medio_pago": "Nequi"})
        result = extract_payment({"medio_texto": "Nequi", "monto_texto": "50.000"}, context)
        self.assertEqual(result.arguments["monto"], "50000")


@override_settings(GEMINI_API_KEY="gemini-fixture", GROQ_API_KEY="groq-fixture", TELEGRAM_TEXT_PROVIDERS="gemini,groq")
class ConversationalPaymentTests(TestCase):
    def setUp(self):
        bot._AI_PROVIDER_FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)
        user = Usuario.objects.create_user("Pago libre prueba", rolid=Rol.objects.create(nombre="Web Master"))
        self.profile = TelegramUsuario.objects.create(usuario=user, telegram_user_id=9844, telegram_chat_id=9844)
        self.client_stub = SimpleNamespace(send_message=MagicMock(), answer_callback=MagicMock())
        MetodoPago.objects.create(codigo="nequi", nombre="Nequi", activo=True)
        MetodoPago.objects.create(codigo="efectivo", nombre="Efectivo", activo=True, es_efectivo=True)

    def message(self, phrase, voice=False, **overrides):
        fields = dict(update_id=19000 + TelegramActualizacion.objects.count(), telegram_user_id=9844,
                      telegram_chat_id=9844, chat_type="private", tipo="VOZ" if voice else "TEXTO",
                      texto="" if voice else phrase, transcripcion=phrase if voice else "")
        return TelegramActualizacion.objects.create(**dict(fields, **overrides))

    def test_voice_story_uses_single_structured_fallback_and_prepares_exact_payment(self):
        phrase = "A Papelería Sol le pasé ochenta y cinco mil novecientos con Nequi, apúntame eso."
        self.assertEqual(expense_request(phrase), (None, False))
        update = self.message(phrase, voice=True)
        result = response({"choices": [{"message": {"content": json.dumps({
            "name": "preparar_registro_pago",
            "arguments": {"concepto": "Papelería Sol", "monto_texto": "ochenta y cinco mil novecientos", "medio_texto": "Nequi"},
        })}}]})
        with patch.object(bot.requests, "post", side_effect=[response({}, 503), result]) as post:
            reply = bot.build_reply(update, self.client_stub)
        self.assertEqual(post.call_count, 2)
        groq = post.call_args.kwargs["json"]
        self.assertEqual(groq["response_format"], {"type": "json_object"})
        self.assertNotIn("tools", groq)
        self.assertIn("monto_texto", groq["messages"][0]["content"])
        self.assertEqual(groq["messages"][-1]["content"], phrase)
        action = TelegramAccionPendiente.objects.get(actualizacion=update)
        self.assertEqual(action.argumentos["concepto"], "PAPELERÍA SOL")
        self.assertEqual(action.argumentos["monto"], "85900.00")
        self.assertEqual(action.argumentos["medio_pago"], "nequi")
        self.assertEqual(reply.reply_markup["inline_keyboard"][0][0]["callback_data"], f"confirm:{action.pk}")
        self.assertFalse(Egreso.objects.exists())
        self.assertIsNone(payment_context.get())

    def test_missing_method_is_asked_once_and_answered_without_repeating_payment(self):
        first = self.message("Lo de la visita técnica fueron 85.900; eso ya lo pagué.")
        with patch.object(bot, "is_feature_enabled", return_value=True), \
                patch.object(bot, "TelegramApiClient", return_value=self.client_stub), \
                patch.object(bot.requests, "post", return_value=gemini_calls(("preparar_registro_pago", {
                    "concepto": "visita técnica", "monto_texto": "85.900",
                }))) as post:
            self.assertTrue(bot.process_next_update())
            first.refresh_from_db()
            self.assertEqual((first.estado, first.intencion), ("PROCESADO", bot.PAYMENT_DIALOGUE))
            self.assertIn("¿Con qué medio pagaste?", first.respuesta)
            self.assertIsNone(self.client_stub.send_message.call_args.kwargs["reply_markup"])
            self.assertFalse(TelegramAccionPendiente.objects.exists())
            answer = self.message("Lo pagué por Nequi.", voice=True)
            self.assertTrue(bot.process_next_update())
        post.assert_called_once()
        action = TelegramAccionPendiente.objects.get(actualizacion=answer)
        self.assertEqual(action.argumentos["concepto"], "VISITA TÉCNICA")
        self.assertEqual(action.argumentos["monto"], "85900.00")
        self.assertFalse(Egreso.objects.exists())

    def test_old_or_interrupted_draft_cannot_populate_another_request(self):
        for kind in ("expired", "other_chat", "interrupted", "new_payment"):
            first = self.message("Registra un pago de 55.000 para mensajería.")
            reply = bot.build_reply(first, self.client_stub)
            TelegramActualizacion.objects.filter(pk=first.pk).update(estado="PROCESADO", intencion=reply.intent, respuesta=reply.text)
            self.assertTrue(TelegramAuditoria.objects.filter(accion=bot.PAYMENT_DIALOGUE, argumentos__actualizacion=first.pk).exists())
            kwargs = {}
            if kind == "expired":
                TelegramActualizacion.objects.filter(pk=first.pk).update(recibido_en=timezone.now() - timedelta(minutes=11))
            elif kind == "other_chat":
                kwargs["telegram_chat_id"] = 9845
            elif kind == "interrupted":
                self.message("Otro tema", estado="ERROR")
            current = self.message("Quiero anotar otro pago, fue por Nequi" if kind == "new_payment" else "Fue por Nequi", **kwargs)
            with self.subTest(kind=kind), patch.object(bot, "_intelligent_function_call", return_value=("", {}, "¿Qué pago?")) as ai:
                self.assertEqual(bot.build_reply(current, self.client_stub).text, "¿Qué pago?")
                ai.assert_called_once()
            self.assertFalse(TelegramAccionPendiente.objects.exists())
