import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .models import Categoria, ConceptoEgreso, Egreso, MetodoPago, Rol, TelegramAccionPendiente, TelegramActualizacion, TelegramUsuario, Usuario
from .services import telegram_bot as bot, telegram_proposals as proposals
from .services.telegram_ai_output import AIOutputError, plan_question, validate_plain_reply
from .test_telegram_ai_output import chat_calls, response


def gemini_text(text):
    return response({"candidates": [{"content": {"parts": [{"text": text}]}}]})


class PhantomConfirmationTests(SimpleTestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="123:test-only")
    def test_transport_refuses_incomplete_required_buttons_before_sending(self):
        client = bot.TelegramApiClient()
        for markup in (None, {}, {"inline_keyboard": []}, {"inline_keyboard": [[]]},
                       {"inline_keyboard": [[{"text": "Confirmar"}]]},
                       {"inline_keyboard": [[{"text": "Confirmar", "callback_data": "x" * 65}]]}):
            with self.subTest(markup=markup), patch.object(client, "_post") as post:
                with self.assertRaises(bot.TelegramBotError):
                    client.send_message(1, "Confirma el pago", reply_markup=markup, require_buttons=True)
                post.assert_not_called()

    @override_settings(TELEGRAM_BOT_TOKEN="123:test-only")
    def test_required_buttons_travel_together_with_confirmation_in_same_request(self):
        client = bot.TelegramApiClient()
        keyboard = {"inline_keyboard": [[{"text": "Confirmar", "callback_data": "confirm:123"},
                                         {"text": "Cancelar", "callback_data": "cancel:123"}]]}
        with patch.object(client, "_post", return_value={}) as post:
            client.send_message(1, "Detalle completo del pago", reply_markup=keyboard, require_buttons=True)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["payload"]["reply_markup"], keyboard)
        self.assertEqual(post.call_args.kwargs["payload"]["text"], "Detalle completo del pago")

    def test_real_phantom_proposals_are_rejected_not_converted_to_buttons(self):
        messages = (
            "¿Confirmas que registre este pago?\nRegistrar ENVUELTOS por $85.900 en Nequi\n\nPulsa Confirmar; la propuesta vence en 10 minutos.",
            "¿Confirmas registrar este pago:\u202fMEDIO\u202fBanco\u202fCaja\u202fSocial,\u202fIMPORTE\u202f$1.027.750,\u202fCONCEPTO\u202fMedicamentos?",
            "¿Confirmas esta devolución de la venta 123?",
            "¿Confirmas que quieres cambiar el horario?",
            "Elige uno de estos botones para responder.",
            "Pulsa el botón Confirmar cambio.",
        )
        for text in messages:
            with self.subTest(text=text):
                with self.assertRaises(AIOutputError) as caught:
                    validate_plain_reply(text)
                self.assertEqual(caught.exception.reason, "missing_action")

    def test_questions_for_actual_missing_data_are_preserved(self):
        for text in ("¿Cuánto pagaste y con qué medio?", "¿Confirmas que el medio de pago es Nequi?", "¿A qué empleado te refieres?", "¿El cambio es solo esa fecha o también para las siguientes repeticiones?"):
            self.assertEqual(validate_plain_reply(text), text)

    def test_json_question_cannot_bypass_confirmation_guard(self):
        with self.assertRaises(AIOutputError):
            plan_question({"pregunta": "¿Confirmas registrar el pago por $54.000?"})

    @override_settings(GEMINI_API_KEY="fake-g", GROQ_API_KEY="fake-q", CEREBRAS_API_KEY="fake-c", OPENROUTER_API_KEY="fake-o")
    def test_all_providers_reject_plain_text_confirmation_without_tool(self):
        text = "¿Confirmas que registre este pago?"
        for function, data in (
            (bot._gemini_function_call, gemini_text(text)),
            (bot._groq_function_call, response({"choices": [{"message": {"content": text}}]})),
            (bot._cerebras_function_call, response({"choices": [{"message": {"content": text}}]})),
            (bot._openrouter_function_call, response({"choices": [{"message": {"content": text}}]})),
        ):
            with self.subTest(provider=function.__name__), patch.object(bot.requests, "post", return_value=data), self.assertRaises(bot.TelegramAIProviderError) as caught:
                function("Registra un pago por agua de 1000 en efectivo")
            self.assertEqual(caught.exception.reason, "missing_action")

    @override_settings(GEMINI_API_KEY="", GROQ_API_KEY="fake-q", CEREBRAS_API_KEY="", OPENROUTER_API_KEY="")
    def test_phantom_confirmation_retries_as_a_real_validated_proposal(self):
        bot._AI_PROVIDER_FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)
        args = {"concepto": "AGUA", "monto": 1000, "medio_pago": "efectivo"}
        initial = response({"choices": [{"message": {"content": "¿Confirmas registrar el pago?"}}]})
        repaired = response({"choices": [{"message": {"content": json.dumps({"name": "preparar_registro_pago", "arguments": args})}}]})
        with patch.object(bot.requests, "post", side_effect=[initial, repaired]) as post, patch.object(bot.time, "sleep"), patch.object(bot, "_execute_tool") as execute:
            self.assertEqual(bot._intelligent_function_call("Registra un pago por agua de 1000 en efectivo"), ("preparar_registro_pago", args, ""))
        self.assertEqual(post.call_count, 2)
        self.assertIn("sin crear una propuesta", post.call_args.kwargs["json"]["messages"][0]["content"])
        self.assertEqual(post.call_args.kwargs["json"]["response_format"], {"type": "json_object"})
        execute.assert_not_called()


@override_settings(GEMINI_API_KEY="fake-g", GROQ_API_KEY="fake-q", CEREBRAS_API_KEY="", OPENROUTER_API_KEY="", TIME_ZONE="America/Bogota", USE_TZ=True)
class ConfirmationRecoveryTests(TestCase):
    def setUp(self):
        bot._AI_PROVIDER_FAILURES.clear()
        self.addCleanup(bot._AI_PROVIDER_FAILURES.clear)
        role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("Bot botones", rolid=role)
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=8891, telegram_chat_id=8891)
        self.client_stub = SimpleNamespace(answer_callback=MagicMock(), send_message=MagicMock())
        MetodoPago.objects.create(codigo="efectivo", nombre="Efectivo", activo=True, es_efectivo=True)
        self.concept = ConceptoEgreso.objects.create(nombre="AGUA")

    def message(self, text, voice=False):
        return TelegramActualizacion.objects.create(update_id=9000 + TelegramActualizacion.objects.count(), tipo="VOZ" if voice else "TEXTO", telegram_user_id=8891, telegram_chat_id=8891, chat_type="private", texto="" if voice else text, transcripcion=text if voice else "")

    def prepare_payment(self, concept="AGUA"):
        reply = bot._execute_tool(self.profile, "preparar_registro_pago", {"concepto": concept, "monto": 1000, "medio_pago": "efectivo"}, self.message("Registra pago"))
        return TelegramAccionPendiente.objects.latest("creado_en"), reply

    def callback(self, action, verb="show", profile=None):
        return bot._handle_callback(SimpleNamespace(texto=f"{verb}:{action.pk}", callback_query_id="test-buttons"), profile or self.profile, self.client_stub)

    @staticmethod
    def callbacks(reply):
        return [item["callback_data"] for row in (reply.reply_markup or {}).get("inline_keyboard", []) for item in row]

    def test_real_failed_audio_example_now_produces_real_buttons_without_saving(self):
        args = {"concepto": "ENVUELTOS", "monto": 85900, "medio_pago": "efectivo"}
        update = self.message("Pago de $85,900 de envueltos en efectivo", voice=True)
        with patch.object(bot.requests, "post", side_effect=[gemini_text("¿Confirmas que registre este pago?\nPulsa Confirmar."), chat_calls(("preparar_registro_pago", args))]):
            reply = bot.build_reply(update, self.client_stub)
        action = TelegramAccionPendiente.objects.get()
        self.assertIn(f"confirm:{action.pk}", self.callbacks(reply))
        self.assertIn("$85.900", reply.text)
        self.assertEqual(action.actualizacion_id, update.pk)
        self.assertFalse(Egreso.objects.exists())
        self.callback(action, "confirm")
        self.callback(action, "confirm")
        self.assertEqual(Egreso.objects.count(), 1)

    def test_worker_delivers_generated_buttons_to_telegram(self):
        args = {"concepto": "AGUA", "monto": 1000, "medio_pago": "efectivo"}
        self.message("Registra 1000 por AGUA en efectivo", voice=True)
        with patch.object(bot, "is_feature_enabled", return_value=True), patch.object(bot, "TelegramApiClient", return_value=self.client_stub), patch.object(bot.requests, "post", return_value=chat_calls(("preparar_registro_pago", args))), patch.object(bot.telegram_providers, "active", return_value=["groq"]):
            self.assertTrue(bot.process_next_update())
        action = TelegramAccionPendiente.objects.get()
        markup = self.client_stub.send_message.call_args.kwargs["reply_markup"]
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], f"confirm:{action.pk}")
        self.assertFalse(Egreso.objects.exists())

    def test_missing_buttons_and_spoken_confirmation_reopen_same_payment(self):
        action, original = self.prepare_payment()
        expires = action.vence_en
        for text in ("/botones", "No veo los botones", "No me salen los botones", "Confirmar."):
            for voice in (False, True):
                with self.subTest(text=text, voice=voice), patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No usar IA")):
                    reply = bot.build_reply(self.message(text, voice), self.client_stub)
                self.assertEqual(self.callbacks(reply), self.callbacks(original))
        action.refresh_from_db()
        self.assertEqual(action.vence_en, expires)
        self.assertEqual(TelegramAccionPendiente.objects.count(), 1)
        self.assertFalse(Egreso.objects.exists())

    def test_recover_concept_options_then_confirm_only_after_choice(self):
        ConceptoEgreso.objects.create(nombre="COCA-COLA")
        action, original = self.prepare_payment("COCACOLA")
        reply = proposals.show_pending_buttons(self.profile)
        self.assertEqual(self.callbacks(reply), self.callbacks(original))
        self.assertNotIn(f"confirm:{action.pk}", self.callbacks(reply))
        bot._handle_callback(SimpleNamespace(texto=f"concept:{action.pk}:0", callback_query_id="choice"), self.profile, self.client_stub)
        current = proposals.show_pending_buttons(self.profile)
        self.assertIn(f"confirm:{action.pk}", self.callbacks(current))
        self.assertIn("COCA-COLA", current.text)
        self.assertFalse(Egreso.objects.exists())

    def test_multiple_proposals_are_selected_before_showing_confirm_buttons(self):
        first, _ = self.prepare_payment()
        second, _ = self.prepare_payment("LUZ")
        reply = proposals.show_pending_buttons(self.profile)
        self.assertIn(f"show:{first.pk}", self.callbacks(reply))
        self.assertIn(f"show:{second.pk}", self.callbacks(reply))
        self.assertFalse(any(value.startswith("confirm:") for value in self.callbacks(reply)))
        opened = self.callback(second)
        self.assertIn(f"confirm:{second.pk}", self.callbacks(opened))
        self.assertNotIn(f"confirm:{first.pk}", self.callbacks(opened))
        self.assertFalse(Egreso.objects.exists())

    def test_catalog_reopens_full_original_detail_then_checks_existing_domain_on_confirm(self):
        original = bot._execute_tool(self.profile, "preparar_cambio_catalogo", {"entidad": "categoria", "operacion": "crear", "campos": [{"campo": "nombre", "valor": "BEBIDAS PRUEBA"}]}, self.message("Crea categoría"))
        action = TelegramAccionPendiente.objects.get()
        self.assertIn(proposals.PRESENTATION_KEY, action.argumentos)
        reply = self.callback(action)
        self.assertIn(original.text, reply.text)
        self.assertIn("Vencimiento original", reply.text)
        self.assertFalse(Categoria.objects.exists())
        self.callback(action, "confirm")
        self.assertTrue(Categoria.objects.filter(nombre="BEBIDAS PRUEBA").exists())

    def test_modified_arguments_cannot_reuse_a_confirmation_with_stale_details(self):
        bot._execute_tool(self.profile, "preparar_cambio_catalogo", {"entidad": "categoria", "operacion": "crear", "campos": [{"campo": "nombre", "valor": "UNO"}]})
        action = TelegramAccionPendiente.objects.get()
        action.argumentos["datos"]["nombre"] = "OTRO"
        action.save(update_fields=["argumentos"])
        self.assertNotIn(f"confirm:{action.pk}", self.callbacks(self.callback(action)))
        self.assertFalse(Categoria.objects.exists())

    def test_other_account_cannot_restore_details_or_buttons(self):
        action, _ = self.prepare_payment()
        other_user = Usuario.objects.create_user("Otro botones", rolid=self.user.rolid)
        other = TelegramUsuario.objects.create(usuario=other_user, telegram_user_id=8892, telegram_chat_id=8892)
        reply = self.callback(action, profile=other)
        self.assertIn("otra cuenta", reply.text)
        self.assertIsNone(reply.reply_markup)
        with self.assertRaises(PermissionDenied):
            proposals.restore_confirmation(other, action)
        self.assertFalse(Egreso.objects.exists())

    def test_permissions_are_rechecked_before_reshowing(self):
        action, _ = self.prepare_payment()
        with patch.object(bot, "_require_access", side_effect=PermissionDenied("No permitido")), self.assertRaises(PermissionDenied):
            self.callback(action)
        self.profile.activo = False
        with self.assertRaises(PermissionDenied):
            proposals.show_pending_buttons(self.profile)

    def test_expired_resolved_or_absent_proposals_do_not_get_confirmation_buttons(self):
        empty = proposals.show_pending_buttons(self.profile)
        self.assertIn("No tienes una propuesta", empty.text)
        self.assertIsNone(empty.reply_markup)
        action, _ = self.prepare_payment()
        action.vence_en = timezone.now() - timedelta(seconds=1)
        action.save(update_fields=["vence_en"])
        self.assertIsNone(self.callback(action).reply_markup)
        action.refresh_from_db()
        self.assertEqual(action.estado, "EXPIRADA")
        fresh, _ = self.prepare_payment()
        self.callback(fresh, "cancel")
        self.assertIsNone(self.callback(fresh).reply_markup)
        self.assertFalse(Egreso.objects.exists())

    def test_legacy_incomplete_summary_is_not_enough_to_confirm(self):
        action = TelegramAccionPendiente.objects.create(telegram_usuario=self.profile, accion="cambio_catalogo", argumentos={"entidad": "categoria", "operacion": "crear", "datos": {"nombre": "NO CREAR"}}, resumen="Resumen recortado", vence_en=timezone.now() + timedelta(minutes=5))
        reply = self.callback(action)
        self.assertNotIn(f"confirm:{action.pk}", self.callbacks(reply))
        self.assertIn(f"cancel:{action.pk}", self.callbacks(reply))
        self.assertIn("detalle completo", reply.text)

    def test_return_edit_and_schedule_recoveries_keep_full_details_and_fixed_expiry(self):
        for kind in ("devolver_venta", "editar_pago", "turno_empleado"):
            with self.subTest(kind=kind):
                intent, label = proposals.ACTION_UI[kind]
                action = TelegramAccionPendiente.objects.create(telegram_usuario=self.profile, accion=kind, argumentos={"payload": {"operacion": "cancelar"}}, resumen="Resumen", vence_en=timezone.now() + timedelta(minutes=5))
                text = "Detalle completo: producto #1, cantidad 2, $10.000; alcance SOLO ESTA FECHA."
                original = bot.BotReply(text, intent, proposal_id=str(action.pk), reply_markup={"inline_keyboard": [[{"text": label, "callback_data": f"confirm:{action.pk}"}]]})
                proposals.remember_confirmation(self.profile, original)
                action.refresh_from_db()
                original_expiry = action.vence_en
                with patch.object(proposals, "_require_action_access") as access:
                    reply = proposals.restore_confirmation(self.profile, action)
                access.assert_called_once_with(self.profile, action)
                self.assertIn(text, reply.text)
                self.assertIn(f"confirm:{action.pk}", self.callbacks(reply))
                if kind == "turno_empleado":
                    self.assertIn("Confirmar cancelación", str(reply.reply_markup))
                action.refresh_from_db()
                self.assertEqual(action.vence_en, original_expiry)

    def test_plain_clarification_does_not_borrow_an_unrelated_pending_button(self):
        self.prepare_payment()
        with patch.object(bot, "_intelligent_function_call", return_value=("", {}, "¿A qué empleado te refieres?")):
            reply = bot.build_reply(self.message("Cambia el horario de un empleado"), self.client_stub)
        self.assertIsNone(reply.reply_markup)
        self.assertFalse(Egreso.objects.exists())

    def test_final_gate_rebuilds_missing_or_wrong_keyboards_and_payment_text(self):
        action, reply = self.prepare_payment()
        for markup in (None, {}, {"inline_keyboard": []},
                       {"inline_keyboard": [[{"text": "Ajeno", "callback_data": "confirm:otra-propuesta"}]]}):
            with self.subTest(markup=markup):
                reply.reply_markup = markup
                reply.text = "Importe incorrecto $900.000"
                delivered = proposals.ensure_proposal_buttons(action.actualizacion, reply)
                self.assertEqual(delivered.proposal_id, str(action.pk))
                self.assertEqual(set(self.callbacks(delivered)), {f"confirm:{action.pk}", f"cancel:{action.pk}"})
                self.assertIn("$1.000", delivered.text)
                self.assertNotIn("900.000", delivered.text)
        self.assertEqual(TelegramAccionPendiente.objects.count(), 1)
        self.assertFalse(Egreso.objects.exists())

    def test_final_gate_does_not_depend_on_payment_text_generator_keyboard(self):
        action, reply = self.prepare_payment()
        with patch.object(bot, "_expense_confirmation_reply", return_value=bot.BotReply("Detalle válido", "preparar_registro_pago")):
            delivered = proposals.ensure_proposal_buttons(action.actualizacion, reply)
        self.assertIn(f"confirm:{action.pk}", self.callbacks(delivered))
        self.assertIn(f"cancel:{action.pk}", self.callbacks(delivered))
        self.assertEqual(delivered.proposal_id, str(action.pk))

    def test_text_without_proposal_identity_never_borrows_another_pending_id(self):
        action, _ = self.prepare_payment()
        fabricated = bot.BotReply("¿Confirmas otro pago?", "preparar_registro_pago")
        with self.assertRaises(bot.TelegramBotError):
            proposals.ensure_proposal_buttons(action.actualizacion, fabricated)
        self.assertEqual(TelegramAccionPendiente.objects.count(), 1)
        self.assertFalse(Egreso.objects.exists())

    def test_final_gate_does_not_repair_buttons_for_other_account(self):
        action, reply = self.prepare_payment()
        other_user = Usuario.objects.create_user("Cuenta ajena", rolid=self.user.rolid)
        TelegramUsuario.objects.create(usuario=other_user, telegram_user_id=8899, telegram_chat_id=8899)
        update = self.message("No veo los botones")
        update.telegram_user_id = update.telegram_chat_id = 8899
        with self.assertRaises(bot.TelegramBotError):
            proposals.ensure_proposal_buttons(update, reply)

    def test_final_gate_restores_concept_choices_without_premature_confirmation(self):
        ConceptoEgreso.objects.create(nombre="COCA-COLA")
        action, reply = self.prepare_payment("COCACOLA")
        reply.reply_markup = None
        delivered = proposals.ensure_proposal_buttons(action.actualizacion, reply)
        self.assertIn(f"concept:{action.pk}:0", self.callbacks(delivered))
        self.assertIn(f"newconcept:{action.pk}", self.callbacks(delivered))
        self.assertIn(f"cancel:{action.pk}", self.callbacks(delivered))
        self.assertNotIn(f"confirm:{action.pk}", self.callbacks(delivered))
        bot._handle_callback(SimpleNamespace(texto=f"concept:{action.pk}:0", callback_query_id="choice-now"), self.profile, self.client_stub)
        current = proposals.ensure_proposal_buttons(action.actualizacion, reply)
        self.assertIn(f"confirm:{action.pk}", self.callbacks(current))
        self.assertNotIn(f"concept:{action.pk}:0", self.callbacks(current))
        self.assertIn("COCA-COLA", current.text)
        self.assertFalse(Egreso.objects.exists())

    def test_catalog_detail_is_saved_even_if_generator_forgets_keyboard(self):
        original_function = bot.TOOL_FUNCTIONS["preparar_cambio_catalogo"]

        def forget_keyboard(*args, **kwargs):
            result = original_function(*args, **kwargs)
            result.reply_markup = None
            return result

        update = self.message("Crear categoría")
        with patch.dict(bot.TOOL_FUNCTIONS, {"preparar_cambio_catalogo": forget_keyboard}):
            reply = bot._execute_tool(self.profile, "preparar_cambio_catalogo", {"entidad": "categoria", "operacion": "crear", "campos": [{"campo": "nombre", "valor": "CON BOTONES"}]}, update)
        action = TelegramAccionPendiente.objects.get()
        self.assertIn(proposals.PRESENTATION_KEY, action.argumentos)
        delivered = proposals.ensure_proposal_buttons(update, reply)
        self.assertIn("CON BOTONES", delivered.text)
        self.assertIn(f"confirm:{action.pk}", self.callbacks(delivered))
        self.assertIn(f"cancel:{action.pk}", self.callbacks(delivered))
        self.assertFalse(Categoria.objects.exists())

    def test_worker_automatically_repairs_keyboard_lost_after_preparation(self):
        update = self.message("Registra 1000 por AGUA en efectivo")
        original_build = bot.build_reply

        def forget_keyboard(*args, **kwargs):
            result = original_build(*args, **kwargs)
            result.reply_markup = None
            return result

        with patch.object(bot, "is_feature_enabled", return_value=True), patch.object(bot, "TelegramApiClient", return_value=self.client_stub), patch.object(bot, "build_reply", side_effect=forget_keyboard), patch.object(bot, "_intelligent_function_call", return_value=("preparar_registro_pago", {"concepto": "AGUA", "monto": 1000, "medio_pago": "efectivo"}, "")):
            self.assertTrue(bot.process_next_update())
        update.refresh_from_db()
        self.assertEqual(update.estado, "PROCESADO")
        self.assertTrue(self.client_stub.send_message.call_args.kwargs["require_buttons"])
        self.assertTrue(self.client_stub.send_message.call_args.kwargs["reply_markup"]["inline_keyboard"])
        self.assertFalse(Egreso.objects.exists())

    def test_send_failure_reuses_same_proposal_automatically_without_new_ai_call(self):
        update = self.message("Registra 1000 por AGUA en efectivo", voice=True)
        self.client_stub.send_message.side_effect = [bot.TelegramExternalError("Error de envío simulado"), None]
        with patch.object(bot, "is_feature_enabled", return_value=True), patch.object(bot, "TelegramApiClient", return_value=self.client_stub), patch.object(bot, "logger"), patch.object(bot, "_intelligent_function_call", return_value=("preparar_registro_pago", {"concepto": "AGUA", "monto": 1000, "medio_pago": "efectivo"}, "")) as intelligent:
            self.assertTrue(bot.process_next_update())
            update.refresh_from_db()
            self.assertEqual(update.estado, "PENDIENTE")
            action = TelegramAccionPendiente.objects.get()
            expiry = action.vence_en
            self.assertTrue(bot.process_next_update())
        update.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual(update.estado, "PROCESADO")
        self.assertEqual(update.intentos, 2)
        self.assertEqual(action.vence_en, expiry)
        self.assertEqual(TelegramAccionPendiente.objects.count(), 1)
        self.assertFalse(Egreso.objects.exists())
        intelligent.assert_called_once()
        keyboards = [call.kwargs["reply_markup"] for call in self.client_stub.send_message.call_args_list]
        self.assertEqual(keyboards[0], keyboards[1])
        self.assertTrue(all(call.kwargs["require_buttons"] for call in self.client_stub.send_message.call_args_list))

    def test_retry_does_not_revive_expired_proposal_or_reinterpret_request(self):
        update = self.message("Registra 1000 por AGUA en efectivo")
        self.client_stub.send_message.side_effect = [bot.TelegramExternalError("Error de envío simulado"), None]
        with patch.object(bot, "is_feature_enabled", return_value=True), patch.object(bot, "TelegramApiClient", return_value=self.client_stub), patch.object(bot, "logger"), patch.object(bot, "_intelligent_function_call", return_value=("preparar_registro_pago", {"concepto": "AGUA", "monto": 1000, "medio_pago": "efectivo"}, "")) as intelligent:
            bot.process_next_update()
            action = TelegramAccionPendiente.objects.get()
            action.vence_en = timezone.now() - timedelta(seconds=1)
            action.save(update_fields=["vence_en"])
            bot.process_next_update()
        update.refresh_from_db()
        self.assertEqual(update.estado, "PROCESADO")
        self.assertIn("venció", update.respuesta)
        self.assertFalse(self.client_stub.send_message.call_args.kwargs["require_buttons"])
        self.assertIsNone(self.client_stub.send_message.call_args.kwargs["reply_markup"])
        self.assertEqual(TelegramAccionPendiente.objects.count(), 1)
        intelligent.assert_called_once()
        self.assertFalse(Egreso.objects.exists())
