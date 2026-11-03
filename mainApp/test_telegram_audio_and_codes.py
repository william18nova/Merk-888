"""Regresiones de los audios reales y claves Merk2 sin servicios/base reales."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from django.contrib.auth.hashers import check_password
from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from .models import (AutorizacionDescuentoEspecial, Cliente, ClienteEspecial, Rol,
                     TelegramAccionPendiente, TelegramActualizacion, TelegramAuditoria,
                     TelegramUsuario, Usuario)
from .services import telegram_bot as bot, telegram_special_codes as codes, telegram_voice as voice
from .services.telegram_ai_policy import selected_tool_names
from .services.telegram_expense_shortcuts import direct_expense_request


class AudioShortcutTests(SimpleTestCase):
    def test_copyable_code_is_not_in_reply_repr(self):
        reply = bot.BotReply('Referencia: MERK-TEST', 'clave_merk2_generada',
                             history_text='Clave generada; omitida del historial.', copyable_code='00001234')
        self.assertNotIn('00001234', repr(reply))
        self.assertNotIn('00001234', str(reply))

    def test_separate_code_message_rejects_invalid_or_untrusted_envelopes_before_sending(self):
        defaults = dict(intent='clave_merk2_generada', history_text='Clave generada.', copyable_code='12345678')
        changes = [{'copyable_code': value} for value in (None, '', '1234', '123456789', 'ABCD5678', 12345678, '١٢٣٤٥٦٧٨')]
        changes += [{'intent': 'respuesta_ia'}, {'history_text': None}, {'history_text': ''}, {'proposal_id': 'test-only'}]
        for change in changes:
            with self.subTest(change=change):
                client = SimpleNamespace(send_message=MagicMock())
                reply = bot.BotReply('Información sin clave.', **dict(defaults, **change))
                with self.assertRaises(bot.TelegramBotError):
                    bot._send_bot_reply(client, 1, reply)
                client.send_message.assert_not_called()

    def test_non_secret_replies_still_use_one_message_and_keep_required_buttons(self):
        client = SimpleNamespace(send_message=MagicMock())
        markup = {'inline_keyboard': [[{'text': 'Confirmar', 'callback_data': 'confirm:test'}]]}
        reply = bot.BotReply('Propuesta de prueba', 'preparar_registro_pago', proposal_id='test', reply_markup=markup)
        bot._send_bot_reply(client, 1, reply)
        client.send_message.assert_called_once_with(1, reply.text, reply_markup=markup, require_buttons=True)

    def test_transport_does_not_log_token_or_message_when_network_fails(self):
        with patch.object(bot, "_configured", return_value="TEST_PRIVATE_TOKEN"):
            client = bot.TelegramApiClient()
        with patch.object(bot.requests, "post", side_effect=requests.Timeout("TEST_PRIVATE_TOKEN PRIVATE_MESSAGE")):
            with self.assertRaises(bot.TelegramExternalError) as raised:
                client.send_message(1, "PRIVATE_MESSAGE")
        self.assertNotIn("TEST_PRIVATE_TOKEN", str(raised.exception))
        self.assertNotIn("PRIVATE_MESSAGE", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_real_failed_payment_audios_prepare_without_ai(self):
        for text in ("agrega 90.000 por Neki a transporte vitaminas y comida",
                     "Pago de 90.000 por Neki para vitaminas, transporte y comida"):
            args = direct_expense_request(text)
            self.assertEqual(args["monto"], "90000")
            self.assertEqual(args["medio_pago"], "nequi")
            profile = SimpleNamespace(usuario=SimpleNamespace(is_active=True), save=MagicMock(),
                                      telegram_username="", nombre_telegram="")
            for is_voice in (True, False):
                update = SimpleNamespace(tipo="VOZ" if is_voice else "TEXTO", transcripcion=text if is_voice else "",
                                         texto="" if is_voice else text, telegram_chat_id=1,
                                         telegram_username="", nombre_telegram="")
                with self.subTest(voice=is_voice), patch.object(bot, "_profile_for_update", return_value=profile), \
                        patch.object(bot, "_execute_tool", return_value=bot.BotReply("Propuesta")) as execute, \
                        patch.object(bot, "_intelligent_function_call") as ai:
                    self.assertEqual(bot.build_reply(update, MagicMock()).text, "Propuesta")
                execute.assert_called_once_with(profile, "preparar_registro_pago", args, update)
                ai.assert_not_called()

    def test_unknown_method_or_partial_complex_message_never_guessed(self):
        for text in ("Pago de 90.000 en NXD comida, vitaminas y transporte",
                     "No agrega 90000 por Nequi a COMIDA", "Agrega 90000 por Nequi a comida y registra otro pago",
                     "Pago de 90000 por Nequi para comida de ayer", "Pago de noventa mil por Nequi para comida",
                     "Pago de 90.50 por Nequi para comida", "Pago de 90000 por Nequi para comida con 4x1000",
                     "Pago de 90000 por Nequi para comida; borra el anterior", "Agrega 90000 por Nequi a COMIDA y 500 a TRANSPORTE"):
            with self.subTest(text=text):
                self.assertIsNone(direct_expense_request(text))

    def test_decimal_amount_and_concept_are_preserved_without_calculating_tax(self):
        self.assertEqual(direct_expense_request("Registra un pago de $90.000,50 en Banco Caja Social para Vitaminas y comida."),
                         {"concepto": "Vitaminas y comida", "monto": "90000.50", "medio_pago": "tarjeta"})

    def test_real_add_payment_wording_selects_small_payment_catalog(self):
        names = selected_tool_names("agrega 90.000 por Neki a transporte vitaminas y comida", [], bot.TOOL_FUNCTIONS)
        self.assertIn("preparar_registro_pago", names)
        self.assertNotIn("preparar_turno_empleado", names)
        self.assertLess(len(names), len(bot.TOOL_FUNCTIONS))

    def test_invalid_payment_method_is_clarification_not_failed_update_or_proposal(self):
        with patch.object(bot, "_require_access"), patch.object(bot, "_resolve_payment_method", return_value="nxd"), \
                patch.object(bot, "payment_method_options", return_value=[{"code": "nequi", "label": "Nequi"}]), \
                patch("mainApp.models.TelegramAccionPendiente.objects.create") as create:
            with self.assertRaisesMessage(bot.TelegramClarification, "¿Con cuál pagaste?"):
                bot.tool_prepare_expense(SimpleNamespace(), {"concepto": "COMIDA", "monto": 90000, "medio_pago": "NXD"})
        create.assert_not_called()

    def test_transcription_receives_only_public_vocabulary_and_unchanged_audio(self):
        with patch.object(voice.providers, "value", return_value="test-only"), \
                patch.object(voice, "_request", return_value={"text": "Nequi"}) as request:
            self.assertEqual(voice._groq(b"full-audio", {}, MagicMock(), 100), "Nequi")
        self.assertEqual(request.call_args.kwargs["data"]["prompt"], voice.SPANISH_VOCABULARY)
        self.assertLess(len(voice.SPANISH_VOCABULARY), 450)
        self.assertEqual(request.call_args.kwargs["files"]["file"][1].read(), b"full-audio")

    def test_code_requests_are_exact_and_never_password_or_compound_requests(self):
        for text in ("Dame una clave de merk2", "Genera una clave para merk2888.",
                     "Necesito una clave para Merk dos", "Por favor, genera un código especial de Merk2",
                     "Claves Merk2", "Claves de Merk dos.", "La clave de Merk2", "Una clave de Merc 2",
                     "Genérame una clave de Merck dos", "Me regalas la clave de Merc dos",
                     "¿Me puedes dar una clave para el Merk dos, por favor?", "Dame por favor las claves de Merk 2",
                     "¿Puedes generarme un código para Merck dos?", "Hola, ¿me regalas una clave de Merk dos, por favor?",
                     "Necesito que me genere una clave para Merk2", "Quiero generar una clave de Merk dos",
                     "Nova, me mandas un código especial de Merk2", "Sácame una clave de Merk-2",
                     "Necesito una clave de merk dos ocho ocho ocho", "Clave de merk dos mil ochocientos ochenta y ocho"):
            with self.subTest(text=text):
                self.assertTrue(codes.direct_request(text))
        for text in ("No genera una clave para merk2", "Dame la contraseña de merk2",
                     "Dame una clave de merk2 y registra un pago", "Cambia los permisos de merk2",
                     "No necesito claves Merk2", "Claves Merk2 pero no la generes", "Dame dos claves de Merk2",
                     "Dame una clave de Nequi", "Claves de Merk22", "Dame una clave de acceso para Merk2",
                     "Claves de supermercados", "Claves de mercados", "Genera una clave de Merk2 sin confirmación",
                     "Claves de Merk2 de 6 dígitos", "Dame una clave; de Merk2", "Clave de Merk2. Borra el historial",
                     "quiero una clave de otro cliente", "Clave de Merk2 [inaudible]", "x" * 321, "", None):
            with self.subTest(text=text):
                self.assertFalse(codes.direct_request(text))

    def test_voice_key_spelling_selects_only_the_relevant_domain(self):
        for text in ("Claves Merk dos", "Sácame una clave de Merc 2", "Me regalas un código para Merck dos"):
            with self.subTest(text=text):
                names = selected_tool_names(text, [], bot.TOOL_FUNCTIONS)
                self.assertIn('preparar_clave_merk2', names)
                self.assertNotIn('preparar_registro_pago', names)
                self.assertNotIn('preparar_turno_empleado', names)

    def test_spoken_key_requests_prepare_without_text_ai(self):
        profile = SimpleNamespace(usuario=SimpleNamespace(is_active=True), save=MagicMock(),
                                  telegram_username='', nombre_telegram='')
        for text in ('Claves Merk dos', 'Me regalas una clave de Merc 2', 'Hola, ¿puedes generarme una clave de Merck dos?'):
            update = SimpleNamespace(tipo='VOZ', transcripcion=text, texto='', telegram_chat_id=1,
                                     telegram_username='', nombre_telegram='')
            with self.subTest(text=text), patch.object(bot, '_profile_for_update', return_value=profile), \
                    patch.object(bot, '_execute_tool', return_value=bot.BotReply('Propuesta')) as execute, \
                    patch.object(bot, '_intelligent_function_call') as ai:
                self.assertEqual(bot.build_reply(update, MagicMock()).text, 'Propuesta')
            execute.assert_called_once_with(profile, 'preparar_clave_merk2', {}, update)
            ai.assert_not_called()


class TelegramSpecialCodeTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user("Web Master claves", rolid=Rol.objects.create(nombre="Web Master"))
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=92001, telegram_chat_id=92001)
        self.special = ClienteEspecial.objects.create(cliente=Cliente.objects.create(nombre="merk2888", apellido="Prueba"))
        self.client = SimpleNamespace(answer_callback=MagicMock(), send_message=MagicMock())

    def prepare(self):
        update = TelegramActualizacion.objects.create(update_id=100 + TelegramActualizacion.objects.count(),
            telegram_user_id=92001, telegram_chat_id=92001, chat_type="private", tipo="TEXTO", texto="/clave_merk2", estado="PROCESADO")
        reply = bot.build_reply(update, self.client)
        return TelegramAccionPendiente.objects.get(pk=reply.proposal_id), reply

    def callback(self, action, verb="confirm"):
        return SimpleNamespace(texto=f"{verb}:{action.pk}", callback_query_id="test-only")

    def test_preparation_has_real_buttons_but_never_generates_code(self):
        with patch.object(codes, "generate_one_time_code") as generate, patch.object(bot, "_intelligent_function_call") as ai:
            action, reply = self.prepare()
        self.assertEqual(action.accion, "generar_clave_merk2")
        self.assertEqual(set(action.argumentos), {"solicitud_id", "_telegram_confirmation"})
        self.assertEqual(reply.reply_markup["inline_keyboard"][0][0]["callback_data"], f"confirm:{action.pk}")
        self.assertFalse(AutorizacionDescuentoEspecial.objects.exists())
        generate.assert_not_called()
        ai.assert_not_called()

    def test_voice_pipeline_transcribes_then_sends_real_proposal_buttons_without_generating(self):
        phrases = ['Claves Merk dos', 'Genérame una clave para Merc 2', '¿Puedes generarme una clave de Merck dos?']
        client = SimpleNamespace(download_voice=MagicMock(return_value=b'fictional-ogg'),
                                 send_message=MagicMock(), answer_callback=MagicMock())
        with patch.object(bot, 'is_feature_enabled', return_value=True), patch.object(bot, 'TelegramApiClient', return_value=client), \
                patch.object(bot, 'transcribe_voice', side_effect=phrases) as transcribe, \
                patch.object(bot, '_intelligent_function_call') as ai, patch.object(codes, 'generate_one_time_code') as generate:
            for index, phrase in enumerate(phrases):
                update = TelegramActualizacion.objects.create(update_id=3000 + index,
                    telegram_user_id=self.profile.telegram_user_id, telegram_chat_id=self.profile.telegram_chat_id,
                    chat_type='private', tipo='VOZ', voice_file_id='test-only')
                self.assertTrue(bot.process_next_update())
                update.refresh_from_db()
                self.assertEqual((update.estado, update.intencion), ('PROCESADO', 'preparar_clave_merk2'))
                self.assertEqual(update.transcripcion, phrase)
                action = TelegramAccionPendiente.objects.get(actualizacion=update)
                self.assertEqual((action.accion, action.estado), ('generar_clave_merk2', 'PENDIENTE'))
                self.assertEqual(client.send_message.call_args.kwargs['reply_markup']['inline_keyboard'][0][0]['callback_data'],
                                 f'confirm:{action.pk}')
                self.assertIn('una sola clave', client.send_message.call_args.args[1])
                self.assertTrue(client.send_message.call_args.kwargs['require_buttons'])
        self.assertEqual(transcribe.call_count, len(phrases))
        self.assertEqual(client.download_voice.call_count, len(phrases))
        ai.assert_not_called()
        generate.assert_not_called()
        self.assertFalse(AutorizacionDescuentoEspecial.objects.exists())

    def test_voice_requests_cannot_bypass_web_master_permission(self):
        self.user.rolid = Rol.objects.create(nombre='Cajero')
        self.user.save(update_fields=['rolid'])
        update = TelegramActualizacion.objects.create(update_id=3100,
            telegram_user_id=self.profile.telegram_user_id, telegram_chat_id=self.profile.telegram_chat_id,
            chat_type='private', tipo='VOZ', voice_file_id='test-only')
        client = SimpleNamespace(download_voice=MagicMock(return_value=b'fictional-ogg'), send_message=MagicMock())
        with patch.object(bot, 'is_feature_enabled', return_value=True), patch.object(bot, 'TelegramApiClient', return_value=client), \
                patch.object(bot, 'transcribe_voice', return_value='Claves de Merk dos'), \
                patch.object(codes, 'generate_one_time_code') as generate, patch.object(bot, 'logger'):
            self.assertTrue(bot.process_next_update())
        update.refresh_from_db()
        self.assertEqual(update.estado, 'ERROR')
        self.assertIn('Web Master', update.error)
        generate.assert_not_called()
        self.assertFalse(TelegramAccionPendiente.objects.exists())
        self.assertFalse(AutorizacionDescuentoEspecial.objects.exists())

    def test_confirmation_generates_once_using_web_service_and_hash_only(self):
        action, _ = self.prepare()
        with patch("mainApp.services.special_discount.secrets.randbelow", return_value=12345678):
            reply = bot._handle_callback(self.callback(action), self.profile, self.client)
        authorization = AutorizacionDescuentoEspecial.objects.get()
        self.assertTrue(check_password("12345678", authorization.secreto_hash))
        self.assertEqual(authorization.solicitud_id, action.argumentos["solicitud_id"])
        self.assertEqual(authorization.expira_en - authorization.generada_en, timedelta(minutes=15))
        self.assertEqual(reply.copyable_code, "12345678")
        self.assertNotIn("12345678", reply.text)
        self.assertNotIn("12345678", reply.history_text)
        self.assertNotIn("12345678", str(list(TelegramAuditoria.objects.values("argumentos", "detalle"))))
        self.assertNotIn("12345678", str(TelegramAccionPendiente.objects.get(pk=action.pk).argumentos))
        self.assertNotIn("12345678", str(self.client.answer_callback.call_args))
        again = bot._handle_callback(self.callback(action), self.profile, self.client)
        self.assertEqual(AutorizacionDescuentoEspecial.objects.count(), 1)
        self.assertNotIn("12345678", again.text)
        self.assertIsNone(again.copyable_code)

    def test_cancel_expiry_and_foreign_owner_cannot_generate(self):
        for scenario in ("cancel", "expired", "foreign"):
            action, _ = self.prepare()
            if scenario == "expired":
                action.vence_en = timezone.now() - timedelta(seconds=1)
                action.save(update_fields=["vence_en"])
            profile = self.profile
            if scenario == "foreign":
                other = Usuario.objects.create_user("Otro Web Master", rolid=self.user.rolid)
                profile = TelegramUsuario.objects.create(usuario=other, telegram_user_id=92002, telegram_chat_id=92002)
            with patch.object(codes, "generate_one_time_code") as generate:
                bot._handle_callback(self.callback(action, "cancel" if scenario == "cancel" else "confirm"), profile, self.client)
            generate.assert_not_called()

    def test_revoked_role_rechecked_at_confirmation(self):
        action, _ = self.prepare()
        self.user.rolid = Rol.objects.create(nombre="Administrador")
        self.user.save(update_fields=["rolid"])
        with self.assertRaises(PermissionDenied), patch.object(codes, "generate_one_time_code") as generate:
            bot._handle_callback(self.callback(action), self.profile, self.client)
        generate.assert_not_called()
        self.assertFalse(AutorizacionDescuentoEspecial.objects.exists())

    def test_non_web_master_or_inactive_profile_cannot_prepare(self):
        self.profile.activo = False
        with self.assertRaises(PermissionDenied):
            codes.prepare_code(self.profile, {})
        self.profile.activo = True
        self.user.rolid = Rol.objects.create(nombre="Cajero")
        with self.assertRaises(PermissionDenied):
            codes.prepare_code(self.profile, {})
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_existing_active_code_is_never_replaced(self):
        first, _ = self.prepare()
        bot._handle_callback(self.callback(first), self.profile, self.client)
        second, _ = self.prepare()
        reply = bot._handle_callback(self.callback(second), self.profile, self.client)
        self.assertIn("Ya existe una clave vigente", reply.text)
        self.assertEqual(AutorizacionDescuentoEspecial.objects.count(), 1)
        self.assertIsNone(AutorizacionDescuentoEspecial.objects.get().revocada_en)

    def test_expired_callback_notice_does_not_prevent_one_time_code_delivery(self):
        action, _ = self.prepare()
        self.client.answer_callback.side_effect = bot.TelegramBotError("callback expired")
        with patch("mainApp.services.special_discount.secrets.randbelow", return_value=12345678):
            reply = bot._handle_callback(self.callback(action), self.profile, self.client)
        self.assertEqual(reply.copyable_code, "12345678")
        self.assertNotIn("12345678", reply.text)
        self.assertEqual(AutorizacionDescuentoEspecial.objects.count(), 1)

    def test_worker_sends_code_but_saves_only_safe_summary(self):
        update = TelegramActualizacion.objects.create(update_id=1234, telegram_user_id=92001, telegram_chat_id=92001,
                                                      chat_type="private", tipo="TEXTO", texto="test-only")
        reply = bot.BotReply("Referencia: MERK-TEST. Un solo uso.", "clave_merk2_generada",
                             history_text="Clave generada. Referencia: MERK-TEST.", copyable_code="12345678")
        with patch.object(bot, "is_feature_enabled", return_value=True), patch.object(bot, "TelegramApiClient", return_value=self.client), \
                patch.object(bot, "build_reply", return_value=reply):
            bot.process_next_update()
        update.refresh_from_db()
        self.assertEqual(update.respuesta, reply.history_text)
        self.assertNotIn("12345678", update.respuesta)
        calls = self.client.send_message.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[1], reply.text)
        self.assertEqual(calls[1].args[1], '12345678')
        self.assertEqual(calls[1].kwargs, {})

    def test_confirmed_key_delivers_two_bubbles_and_preserves_leading_zeros(self):
        action, _ = self.prepare()
        update = TelegramActualizacion.objects.create(update_id=1236, telegram_user_id=92001, telegram_chat_id=92001,
            chat_type='private', tipo='CALLBACK', texto=f'confirm:{action.pk}', callback_query_id='test-only')
        with patch.object(bot, 'is_feature_enabled', return_value=True), patch.object(bot, 'TelegramApiClient', return_value=self.client), \
                patch('mainApp.services.special_discount.secrets.randbelow', return_value=1234):
            self.assertTrue(bot.process_next_update())
        update.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual((update.estado, action.estado), ('PROCESADO', 'CONFIRMADA'))
        self.assertEqual(AutorizacionDescuentoEspecial.objects.count(), 1)
        calls = self.client.send_message.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].args[0], calls[1].args[0])
        self.assertIn('Referencia:', calls[0].args[1])
        self.assertIn('Vence el', calls[0].args[1])
        self.assertNotIn('00001234', calls[0].args[1])
        self.assertEqual(calls[1].args[1], '00001234')
        self.assertNotIn('00001234', update.respuesta + update.error)
        self.assertNotIn('00001234', str(list(TelegramAuditoria.objects.values('argumentos', 'detalle'))))

    def test_uncertain_second_bubble_does_not_regenerate_retry_or_log_secret(self):
        action, _ = self.prepare()
        update = TelegramActualizacion.objects.create(update_id=1237, telegram_user_id=92001, telegram_chat_id=92001,
            chat_type='private', tipo='CALLBACK', texto=f'confirm:{action.pk}', callback_query_id='test-only')
        self.client.send_message.side_effect = [None, bot.TelegramExternalError('PRIVATE 12345678'), None]
        with patch.object(bot, 'is_feature_enabled', return_value=True), patch.object(bot, 'TelegramApiClient', return_value=self.client), \
                patch('mainApp.services.special_discount.secrets.randbelow', return_value=12345678), \
                self.assertLogs(bot.logger, level='ERROR') as logged:
            self.assertTrue(bot.process_next_update())
            self.assertFalse(bot.process_next_update())
        update.refresh_from_db()
        action.refresh_from_db()
        self.assertEqual((update.estado, action.estado), ('ERROR', 'CONFIRMADA'))
        self.assertEqual(AutorizacionDescuentoEspecial.objects.count(), 1)
        calls = self.client.send_message.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1].args[1], '12345678')
        self.assertIn('La clave se generó', calls[2].args[1])
        self.assertIn('revoca', calls[2].args[1])
        self.assertNotIn('12345678', update.respuesta + update.error + calls[0].args[1] + calls[2].args[1])
        self.assertNotIn('12345678', str(logged.output))
        self.assertNotIn('PRIVATE', str(logged.output))

    def test_uncertain_send_never_generates_another_code_or_saves_plaintext(self):
        action, _ = self.prepare()
        update = TelegramActualizacion.objects.create(update_id=1235, telegram_user_id=92001, telegram_chat_id=92001,
            chat_type="private", tipo="CALLBACK", texto=f"confirm:{action.pk}", callback_query_id="test-only")
        self.client.send_message.side_effect = [bot.TelegramExternalError("Entrega incierta"), None]
        with patch.object(bot, "is_feature_enabled", return_value=True), patch.object(bot, "TelegramApiClient", return_value=self.client), \
                patch("mainApp.services.special_discount.secrets.randbelow", return_value=12345678), patch.object(bot, "logger"):
            self.assertTrue(bot.process_next_update())
            self.assertTrue(bot.process_next_update())
        update.refresh_from_db()
        self.assertEqual(update.estado, "PROCESADO")
        self.assertEqual(AutorizacionDescuentoEspecial.objects.count(), 1)
        self.assertNotIn("12345678", self.client.send_message.call_args_list[0].args[1])
        self.assertNotIn("12345678", self.client.send_message.call_args_list[1].args[1])
        self.assertNotIn("12345678", update.respuesta + update.error)

    def test_free_text_confirmation_only_restores_proposal(self):
        action, _ = self.prepare()
        update = SimpleNamespace(tipo="VOZ", transcripcion="Confirmar.", texto="", telegram_chat_id=92001,
                                 telegram_username="", nombre_telegram="")
        with patch.object(bot, "_profile_for_update", return_value=self.profile), patch.object(codes, "generate_one_time_code") as generate:
            reply = bot.build_reply(update, self.client)
        generate.assert_not_called()
        self.assertEqual(reply.proposal_id, str(action.pk))

    def test_custom_parameters_cannot_change_code_security(self):
        for args in ({"ttl": 9999}, {"usuario": "otro"}, {"clave": "12345678"}):
            with self.assertRaises(bot.TelegramBotError):
                bot._execute_tool(self.profile, "preparar_clave_merk2", args)
        self.assertFalse(TelegramAccionPendiente.objects.exists())
