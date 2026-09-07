from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from .models import (
    ConceptoEgreso, Egreso, Rol, TelegramAccionPendiente, TelegramActualizacion,
    TelegramAuditoria, TelegramUsuario, Usuario,
)
from .services.telegram_bot import (
    GEMINI_TOOLS, GROQ_CHAT_TOOLS, TelegramBotError, _assistant_system_prompt,
    _execute_tool, _handle_callback, build_reply,
)


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True)
class TelegramExpenseResponseModeTests(TestCase):
    def setUp(self):
        role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("Prueba respuestas breves", rolid=role)
        self.profile = TelegramUsuario.objects.create(
            usuario=self.user, telegram_user_id=9301, telegram_chat_id=9301,
        )
        concept = ConceptoEgreso.objects.create(nombre="COCA-COLA")
        self.cash = Egreso.objects.create(
            concepto=concept, monto=Decimal("1000.25"), medio_pago="efectivo",
            registrado_por_nombre="William",
        )
        self.nequi = Egreso.objects.create(
            concepto=concept, monto=Decimal("2000"), medio_pago="nequi",
            registrado_por_nombre="Otra persona",
        )
        self.client_stub = SimpleNamespace(answer_callback=MagicMock())

    def query(self, **arguments):
        return _execute_tool(self.profile, "consultar_pagos", arguments)

    def test_default_response_is_only_total_with_period(self):
        reply = self.query()
        self.assertIn("se han pagado $3.000,25", reply.text)
        self.assertNotIn("\n", reply.text)
        for unrequested in ("Efectivo", "Nequi", "COCA-COLA", "registro(s)", "Detalle"):
            self.assertNotIn(unrequested, reply.text)
        self.assertIsNone(reply.reply_markup)

    def test_empty_period_returns_only_zero(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        reply = self.query(desde=tomorrow, hasta=tomorrow)
        self.assertIn("Total pagado: $0", reply.text)
        self.assertNotIn("\n", reply.text)

    def test_explicit_breakdown_contains_methods_but_no_individual_list(self):
        reply = self.query(desglose_por_medio=True)
        self.assertIn("Total pagado: $3.000,25", reply.text)
        self.assertIn("Efectivo: $1.000,25", reply.text)
        self.assertIn("Nequi: $2.000", reply.text)
        for unrequested in ("Detalle", "COCA-COLA", "Registró", "registro(s)"):
            self.assertNotIn(unrequested, reply.text)

    def test_explicit_list_does_not_add_method_totals(self):
        reply = self.query(detalle=True)
        self.assertIn("Detalle", reply.text)
        self.assertIn(f"#{self.cash.pk}", reply.text)
        self.assertIn("Registró: William", reply.text)
        self.assertNotIn("Así se reparten por medio de pago", reply.text)
        self.assertNotIn("Efectivo:", reply.text)

    def test_list_and_breakdown_can_be_requested_together(self):
        reply = self.query(detalle=True, desglose_por_medio=True)
        self.assertIn("Así se reparten por medio de pago", reply.text)
        self.assertIn("Detalle", reply.text)

    def test_filtering_one_method_does_not_implicitly_request_breakdown(self):
        reply = self.query(medio_pago="nequi")
        self.assertIn("se han pagado $2.000", reply.text)
        self.assertIn("Medio: Nequi", reply.text)
        self.assertNotIn("Así se reparten", reply.text)
        self.assertNotIn("Detalle", reply.text)

    def test_follow_up_keeps_dates_and_filters_without_carrying_list_format(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        Egreso.objects.filter(pk=self.cash.pk).update(creado_en=timezone.now() - timedelta(days=1))
        self.query(
            desde=yesterday.isoformat(), hasta=yesterday.isoformat(),
            concepto="COCA", usuario="William", medio_pago="efectivo", monto_min=500,
            detalle=True,
        )
        with patch("mainApp.services.telegram_bot.timezone.localdate", return_value=yesterday + timedelta(days=3)):
            reply = self.query(usar_consulta_anterior=True, desglose_por_medio=True)
        self.assertIn(yesterday.strftime("%d/%m/%Y"), reply.text)
        self.assertIn("Efectivo: $1.000,25", reply.text)
        self.assertNotIn("Detalle", reply.text)
        audit = TelegramAuditoria.objects.filter(accion="consultar_pagos", exitoso=True).first()
        self.assertEqual(audit.argumentos["usuario"], "William")
        self.assertEqual(audit.argumentos["monto_min"], 500)
        self.assertNotIn("detalle", audit.argumentos)

    def test_follow_up_can_return_to_only_total(self):
        self.query(detalle=True, desglose_por_medio=True)
        reply = self.query(usar_consulta_anterior=True)
        self.assertNotIn("\n", reply.text)
        self.assertIn("se han pagado $3.000,25", reply.text)

    def test_follow_up_can_change_an_explicit_filter(self):
        self.query(medio_pago="nequi")
        reply = self.query(usar_consulta_anterior=True, medio_pago="efectivo")
        self.assertIn("se han pagado $1.000,25", reply.text)
        self.assertNotIn("Nequi", reply.text)

    def test_new_question_does_not_inherit_previous_filters(self):
        self.query(medio_pago="nequi")
        reply = self.query()
        self.assertIn("se han pagado $3.000,25", reply.text)

    def test_follow_up_without_recent_context_requests_a_new_interval(self):
        with self.assertRaisesMessage(TelegramBotError, "consulta de pagos reciente"):
            self.query(usar_consulta_anterior=True)
        self.query()
        TelegramAuditoria.objects.update(creado_en=timezone.now() - timedelta(hours=25))
        with self.assertRaisesMessage(TelegramBotError, "consulta de pagos reciente"):
            self.query(usar_consulta_anterior=True)

    def test_other_accounts_and_chats_cannot_supply_follow_up_context(self):
        self.query(medio_pago="nequi")
        self.profile.telegram_chat_id = 9399
        with self.assertRaisesMessage(TelegramBotError, "consulta de pagos reciente"):
            self.query(usar_consulta_anterior=True)
        self.profile.telegram_chat_id = 9301
        TelegramAuditoria.objects.update(telegram_user_id=9399)
        with self.assertRaisesMessage(TelegramBotError, "consulta de pagos reciente"):
            self.query(usar_consulta_anterior=True)

    def test_permissions_are_rechecked_for_follow_up(self):
        self.query()
        self.user.is_active = False
        with self.assertRaises(PermissionDenied):
            self.query(usar_consulta_anterior=True, detalle=True)

    def test_legacy_unanchored_query_uses_date_of_original_request(self):
        self.query()
        yesterday_time = timezone.now() - timedelta(hours=12)
        TelegramAuditoria.objects.filter(accion="consultar_pagos").update(
            argumentos={}, creado_en=yesterday_time,
        )
        reply = self.query(usar_consulta_anterior=True)
        self.assertIn(timezone.localtime(yesterday_time).strftime("%d/%m/%Y"), reply.text)

    def test_old_pagination_buttons_still_open_lists(self):
        for _ in range(4):
            Egreso.objects.create(
                concepto=self.cash.concepto, monto=1, medio_pago="efectivo",
                registrado_por_nombre="Prueba",
            )
        self.query(detalle=True)
        audit = TelegramAuditoria.objects.filter(accion="consultar_pagos").first()
        del audit.argumentos["detalle"]
        audit.save(update_fields=["argumentos"])
        callback = SimpleNamespace(texto=f"page:{audit.pk}:2", callback_query_id="legacy-page")
        reply = _handle_callback(callback, self.profile, self.client_stub)
        self.assertIn("Detalle · página 2 de 2", reply.text)

    def test_ai_schema_and_prompt_expose_independent_response_options(self):
        definitions = [
            GEMINI_TOOLS[0]["functionDeclarations"],
            [row["function"] for row in GROQ_CHAT_TOOLS],
        ]
        for tools in definitions:
            properties = next(row for row in tools if row["name"] == "consultar_pagos")["parameters"]["properties"]
            for option in ("detalle", "desglose_por_medio", "usar_consulta_anterior"):
                self.assertIn(option, properties)
        prompt = _assistant_system_prompt()
        self.assertIn("cuánto he pagado hoy", prompt)
        self.assertIn("desglose_por_medio=false", prompt)

    def test_text_and_audio_route_to_the_requested_format_without_recording_payments(self):
        cases = (
            ("Cuánto he pagado hoy", {}, False, False),
            ("Ahora sepáralo por método de pago", {"usar_consulta_anterior": True, "desglose_por_medio": True}, True, False),
            ("Ahora muéstrame la lista", {"usar_consulta_anterior": True, "detalle": True}, False, True),
        )
        update_id = 93000
        for kind in ("TEXTO", "VOZ"):
            for text, arguments, methods, details in cases:
                update_id += 1
                update = TelegramActualizacion.objects.create(
                    update_id=update_id, telegram_user_id=9301, telegram_chat_id=9301,
                    chat_type="private", tipo=kind,
                    texto=text if kind == "TEXTO" else "",
                    transcripcion=text if kind == "VOZ" else "",
                )
                with patch("mainApp.services.telegram_bot._intelligent_function_call", return_value=(
                    "consultar_pagos", arguments, "",
                )):
                    reply = build_reply(update, self.client_stub)
                self.assertEqual("Así se reparten por medio de pago" in reply.text, methods)
                self.assertEqual("Detalle" in reply.text, details)
        self.assertEqual(Egreso.objects.count(), 2)
        self.assertFalse(TelegramAccionPendiente.objects.exists())
