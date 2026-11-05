"""Misma petición por texto o transcripción, sin APIs ni datos reales."""
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase

from .services import telegram_bot as bot
from .services.telegram_assistant import common_read_request
from .services.telegram_expense_shortcuts import direct_expense_request, expense_clarification
from .services.telegram_shortcuts import specific_read_request
from .services.telegram_special_codes import direct_request as special_code_request


TODAY = date(2026, 11, 5)
TODAY_ARGS = {"desde": "2026-11-05", "hasta": "2026-11-05"}
YESTERDAY_ARGS = {"desde": "2026-11-04", "hasta": "2026-11-04"}
WEEK_ARGS = {"desde": "2026-11-02", "hasta": "2026-11-05"}


def message(phrase, voice):
    return SimpleNamespace(tipo="VOZ" if voice else "TEXTO", texto="" if voice else phrase,
                           transcripcion=phrase if voice else "", telegram_chat_id=1,
                           telegram_username="", nombre_telegram="")


class TelegramFastPathTests(SimpleTestCase):
    def setUp(self):
        self.profile = SimpleNamespace(usuario=SimpleNamespace(is_active=True), save=MagicMock(),
                                       telegram_username="", nombre_telegram="")

    def test_unambiguous_phrases_keep_exact_filters_without_text_ai(self):
        cases = (
            ("¿Cuánto llevamos vendido hoy?", "consultar_ventas", TODAY_ARGS),
            ("Dime en cuánto van las ventas de hoy.", "consultar_ventas", TODAY_ARGS),
            ("¿Cuál es el total de ventas de ayer por medio de pago?", "consultar_ventas",
             dict(YESTERDAY_ARGS, desglose_por_medio=True)),
            ("Hola, ¿me puedes decir cuánto llevamos pagado esta semana por Nequi?", "consultar_pagos",
             dict(WEEK_ARGS, medio_pago="nequi", detalle=False, desglose_por_medio=False)),
            ("Muéstrame los pagos en efectivo de ayer.", "consultar_pagos",
             dict(YESTERDAY_ARGS, medio_pago="efectivo", detalle=True, desglose_por_medio=False)),
            ("Pagos en Daviplata de esta semana.", "consultar_pagos",
             dict(WEEK_ARGS, medio_pago="daviplata", detalle=True, desglose_por_medio=False)),
            ("Consulta el inventario del producto Arroz Diana 500 g.", "consultar_inventario", {"consulta": "arroz diana 500 g"}),
            ("¿Cuántas unidades de Café Sello Rojo quedan en inventario?", "consultar_inventario", {"consulta": "cafe sello rojo"}),
            ("¿Qué stock hay del producto leche entera?", "consultar_inventario", {"consulta": "leche entera"}),
            ("Consulta el stock del producto El Rey", "consultar_inventario", {"consulta": "el rey"}),
            ("¿Puedes conseguirme una clave para el cliente Merk dos?", "preparar_clave_merk2", {}),
            ("¿Cuánto hemos vendido hoy?", "consultar_ventas", TODAY_ARGS),
            ("Claves de Merk dos", "preparar_clave_merk2", {}),
            ("Registra un pago de 50.000 en efectivo para mensajería", "preparar_registro_pago",
             {"concepto": "mensajería", "monto": "50000", "medio_pago": "efectivo"}),
        )
        for phrase, tool, arguments in cases:
            for voice in (False, True):
                update = message(phrase, voice)
                reply = bot.BotReply("Respuesta de la herramienta")
                with self.subTest(phrase=phrase, voice=voice), \
                        patch.object(bot, "_profile_for_update", return_value=self.profile), \
                        patch.object(bot, "_execute_tool", return_value=reply) as execute, \
                        patch.object(bot, "_intelligent_function_call") as ai, \
                        patch.object(bot.timezone, "localdate", return_value=TODAY):
                    self.assertIs(bot.build_reply(update, MagicMock()), reply)
                    execute.assert_called_once()
                    self.assertEqual(execute.call_args.args[:3], (self.profile, tool, arguments))
                    self.assertIs(execute.call_args.kwargs.get("update") or execute.call_args.args[3], update)
                    ai.assert_not_called()

    def test_incomplete_payment_asks_without_tool_call_or_buttons(self):
        cases = (
            ("Registra un pago de 50.000 para mensajería.", "¿Con qué medio pagaste?"),
            ("Registra un pago de 50.000 en efectivo.", "¿Qué concepto uso para ese pago?"),
            ("Registra un pago de 50.000 en efectivo para", "¿Qué concepto uso para ese pago?"),
        )
        for phrase, question in cases:
            for voice in (False, True):
                with self.subTest(phrase=phrase, voice=voice), \
                        patch.object(bot, "_profile_for_update", return_value=self.profile), \
                        patch.object(bot, "_require_access") as access, \
                        patch.object(bot, "_execute_tool") as execute, \
                        patch.object(bot, "_intelligent_function_call") as ai:
                    reply = bot.build_reply(message(phrase, voice), MagicMock())
                    self.assertTrue(reply.text.startswith(question))
                    self.assertEqual(reply.intent, "aclarar_pago")
                    self.assertIsNone(reply.reply_markup)
                    self.assertFalse(reply.proposal_id)
                    access.assert_called_once_with(self.profile, "registrar_egreso")
                    execute.assert_not_called()
                    ai.assert_not_called()

    def test_payment_clarification_does_not_bypass_role(self):
        with patch.object(bot, "_profile_for_update", return_value=self.profile), \
                patch.object(bot, "user_can_access_url_name", return_value=False), \
                patch.object(bot, "_execute_tool") as execute, patch.object(bot, "_intelligent_function_call") as ai:
            with self.assertRaises(PermissionDenied):
                bot.build_reply(message("Registra un pago de 50.000 en efectivo", True), MagicMock())
        execute.assert_not_called()
        ai.assert_not_called()

    def test_complex_and_ambiguous_phrases_never_truncate_to_a_fast_path(self):
        for phrase in (
            "¿Cuánto llevamos vendido hoy y quién vendió más?",
            "¿Cuánto vendí hoy?",
            "Muéstrame los pagos en efectivo de ayer menos transporte.",
            "Pagos en Daviplata de esta semana de William.",
            "Pagos",
            "Consulta el inventario de Yerbabuena.",
            "Consulta el stock del producto Arroz Diana en Yerbabuena.",
            "Consulta el stock del producto Arroz Diana solo en Yerbabuena.",
            "Consulta el inventario de esta semana.",
            "Consulta el inventario de productos agotados.",
            "Cuántas unidades de Arroz Diana o Arroz Roa quedan en inventario",
            "Cuántas unidades de arroz no quedan en inventario",
            "¿Puedes conseguirme una clave para el cliente Merk dos sin confirmar?",
            "Registra un pago de 50.000 para mensajería por otro medio.",
            "Registra un pago de 50.000 en efectivo de esta semana.",
            "Registra un pago de 50.000 en NXD.",
            "Registra un pago de $50,000 para mensajería.",
            "No registres un pago de 50.000 para mensajería.",
            "En efectivo",
        ):
            with self.subTest(phrase=phrase):
                self.assertIsNone(common_read_request(phrase))
                self.assertIsNone(specific_read_request(phrase))
                self.assertIsNone(direct_expense_request(phrase))
                self.assertIsNone(expense_clarification(phrase))
                self.assertFalse(special_code_request(phrase))
