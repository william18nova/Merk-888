import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    ConfiguracionFuncionalidad,
    Egreso,
    MetodoPago,
    Rol,
    TelegramAccionPendiente,
    TelegramActualizacion,
    TelegramCodigoVinculacion,
    TelegramUsuario,
    Usuario,
)
from .services.feature_flags import TELEGRAM_BOT_FEATURE, clear_feature_cache
from .services.telegram_bot import (
    _gemini_function_call,
    _groq_function_call,
    _intelligent_function_call,
    _handle_callback,
    generate_link_code,
    link_telegram_identity,
    process_next_update,
    tool_prepare_expense,
)


@override_settings(
    TELEGRAM_BOT_TOKEN="123:test-token",
    TELEGRAM_WEBHOOK_SECRET="webhook-secret-for-tests",
    GEMINI_API_KEY="gemini-test-key",
    GROQ_API_KEY="groq-test-key",
    GROQ_CHAT_MODEL="llama-3.3-70b-versatile",
)
class TelegramBotTests(TestCase):
    def setUp(self):
        self.webmaster_role = Rol.objects.create(nombre="Web Master")
        self.cashier_role = Rol.objects.create(nombre="Cajero")
        self.webmaster = Usuario.objects.create_user(
            nombreusuario="webmaster-telegram",
            password="safe-test-password",
            rolid=self.webmaster_role,
        )
        self.cashier = Usuario.objects.create_user(
            nombreusuario="cashier-telegram",
            password="safe-test-password",
            rolid=self.cashier_role,
        )
        ConfiguracionFuncionalidad.objects.update_or_create(
            clave=TELEGRAM_BOT_FEATURE,
            defaults={"habilitada": True},
        )
        clear_feature_cache(TELEGRAM_BOT_FEATURE)
        MetodoPago.objects.update_or_create(
            codigo="efectivo",
            defaults={
                "nombre": "Efectivo",
                "activo": True,
                "es_efectivo": True,
                "es_sistema": True,
                "orden": 10,
            },
        )

    def tearDown(self):
        clear_feature_cache(TELEGRAM_BOT_FEATURE)

    def test_link_code_is_one_use_and_only_hash_is_persisted(self):
        code, expires = generate_link_code(
            user=self.cashier,
            created_by=self.webmaster,
        )
        row = TelegramCodigoVinculacion.objects.get(usuario=self.cashier)
        self.assertNotEqual(row.codigo_hash, code)
        self.assertNotIn(code, row.codigo_hash)
        self.assertGreater(expires, timezone.now())

        profile = link_telegram_identity(
            code=code,
            telegram_user_id=9001,
            chat_id=9001,
            username="cashier",
            name="Cajero Uno",
        )
        self.assertEqual(profile.usuario, self.cashier)
        with self.assertRaisesMessage(Exception, "ya fue usado"):
            link_telegram_identity(
                code=code,
                telegram_user_id=9001,
                chat_id=9001,
            )

    def test_webhook_requires_secret_and_deduplicates_update_id(self):
        payload = {
            "update_id": 70001,
            "message": {
                "from": {"id": 9001, "username": "cashier", "first_name": "Caja"},
                "chat": {"id": 9001, "type": "private"},
                "text": "/estado",
            },
        }
        client = Client()
        denied = client.post(
            reverse("telegram_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong",
        )
        self.assertEqual(denied.status_code, 403)

        first = client.post(
            reverse("telegram_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="webhook-secret-for-tests",
        )
        second = client.post(
            reverse("telegram_webhook"),
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="webhook-secret-for-tests",
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(TelegramActualizacion.objects.filter(pk=70001).count(), 1)

    def test_confirmed_expense_is_executed_exactly_once(self):
        profile = TelegramUsuario.objects.create(
            usuario=self.webmaster,
            telegram_user_id=9002,
            telegram_chat_id=9002,
        )
        original = TelegramActualizacion.objects.create(
            update_id=70002,
            telegram_user_id=9002,
            telegram_chat_id=9002,
            chat_type="private",
            tipo="TEXTO",
        )
        proposed = tool_prepare_expense(
            profile,
            {"concepto": "servicio de agua", "monto": "25000", "medio_pago": "efectivo"},
            original,
        )
        action = TelegramAccionPendiente.objects.get()
        self.assertIn(str(action.pk), proposed.reply_markup["inline_keyboard"][0][0]["callback_data"])
        callback = TelegramActualizacion.objects.create(
            update_id=70003,
            telegram_user_id=9002,
            telegram_chat_id=9002,
            chat_type="private",
            tipo="CALLBACK",
            texto=f"confirm:{action.pk}",
            callback_query_id="callback-1",
        )
        telegram_client = SimpleNamespace(answer_callback=MagicMock())
        first = _handle_callback(callback, profile, telegram_client)
        second = _handle_callback(callback, profile, telegram_client)

        self.assertIn("registrado correctamente", first.text)
        self.assertIn("ya estaba confirmada", second.text)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(Egreso.objects.get().concepto.nombre, "SERVICIO DE AGUA")
        action.refresh_from_db()
        self.assertEqual(action.estado, "CONFIRMADA")

    def test_worker_processes_linked_slash_command_without_ai(self):
        TelegramUsuario.objects.create(
            usuario=self.cashier,
            telegram_user_id=9003,
            telegram_chat_id=9003,
        )
        update = TelegramActualizacion.objects.create(
            update_id=70004,
            telegram_user_id=9003,
            telegram_chat_id=9003,
            chat_type="private",
            tipo="TEXTO",
            texto="/estado",
        )
        fake_client = SimpleNamespace(send_message=MagicMock())
        with patch("mainApp.services.telegram_bot.TelegramApiClient", return_value=fake_client):
            self.assertTrue(process_next_update())
        update.refresh_from_db()
        self.assertEqual(update.estado, "PROCESADO")
        self.assertEqual(update.intencion, "estado")
        fake_client.send_message.assert_called_once()

    def test_gemini_key_is_header_and_function_call_is_parsed(self):
        response = SimpleNamespace(
            status_code=200,
            ok=True,
            json=lambda: {
                "candidates": [{
                    "content": {"parts": [{
                        "functionCall": {
                            "name": "buscar_producto",
                            "args": {"consulta": "tomate"},
                        }
                    }]}
                }]
            },
        )
        with patch("mainApp.services.telegram_bot.requests.post", return_value=response) as post:
            name, arguments, text = _gemini_function_call("¿Cuánto vale el tomate?")
        self.assertEqual(name, "buscar_producto")
        self.assertEqual(arguments, {"consulta": "tomate"})
        self.assertEqual(text, "")
        self.assertEqual(post.call_args.kwargs["headers"]["x-goog-api-key"], "gemini-test-key")
        self.assertNotIn("gemini-test-key", post.call_args.args[0])

    def test_groq_key_is_bearer_and_function_call_is_parsed(self):
        response = SimpleNamespace(
            status_code=200,
            ok=True,
            json=lambda: {
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "function": {
                                "name": "buscar_producto",
                                "arguments": json.dumps({"consulta": "tomate"}),
                            }
                        }]
                    }
                }]
            },
        )
        with patch("mainApp.services.telegram_bot.requests.post", return_value=response) as post:
            name, arguments, text = _groq_function_call("¿Cuánto vale el tomate?")
        self.assertEqual(name, "buscar_producto")
        self.assertEqual(arguments, {"consulta": "tomate"})
        self.assertEqual(text, "")
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer groq-test-key",
        )
        self.assertNotIn("groq-test-key", post.call_args.args[0])

    def test_groq_is_used_when_gemini_rejects_request(self):
        rejected = SimpleNamespace(
            status_code=401,
            ok=False,
            json=lambda: {"error": {"message": "Gemini unavailable"}},
        )
        accepted = SimpleNamespace(
            status_code=200,
            ok=True,
            json=lambda: {
                "choices": [{"message": {"content": "Listo, dime qué necesitas."}}]
            },
        )
        with patch(
            "mainApp.services.telegram_bot.requests.post",
            side_effect=[rejected, accepted],
        ) as post:
            name, arguments, text = _intelligent_function_call("Hola")
        self.assertEqual(name, "")
        self.assertEqual(arguments, {})
        self.assertEqual(text, "Listo, dime qué necesitas.")
        self.assertEqual(post.call_count, 2)

    def test_only_webmaster_can_open_configuration(self):
        client = Client()
        client.force_login(self.cashier)
        denied = client.get(reverse("configuracion_telegram_bot"))
        self.assertEqual(denied.status_code, 302)
        self.assertEqual(denied.url, reverse("home"))
        client.force_login(self.webmaster)
        response = client.get(reverse("configuracion_telegram_bot"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Bot inteligente de Telegram")

    def test_expired_confirmation_does_not_create_expense(self):
        profile = TelegramUsuario.objects.create(
            usuario=self.webmaster,
            telegram_user_id=9004,
            telegram_chat_id=9004,
        )
        action = TelegramAccionPendiente.objects.create(
            telegram_usuario=profile,
            accion="registrar_pago",
            argumentos={"concepto": "LUZ", "monto": "1000", "medio_pago": "efectivo"},
            resumen="Pago vencido",
            vence_en=timezone.now() - timedelta(seconds=1),
        )
        callback = TelegramActualizacion.objects.create(
            update_id=70005,
            telegram_user_id=9004,
            telegram_chat_id=9004,
            chat_type="private",
            tipo="CALLBACK",
            texto=f"confirm:{action.pk}",
            callback_query_id="callback-expired",
        )
        reply = _handle_callback(
            callback,
            profile,
            SimpleNamespace(answer_callback=MagicMock()),
        )
        self.assertIn("venció", reply.text)
        self.assertEqual(Egreso.objects.count(), 0)
