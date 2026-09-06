from datetime import datetime, timedelta, timezone as utc_timezone
from decimal import Decimal
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings

from .models import (
    ConceptoEgreso, Egreso, Empleado, Rol, Sucursal, TelegramAccionPendiente,
    TelegramActualizacion, TelegramAuditoria, TelegramUsuario, Usuario,
)
from .services.telegram_bot import (
    GEMINI_TOOLS, GROQ_CHAT_TOOLS, TelegramBotError, _execute_tool,
    _handle_callback, _validated_ai_call, build_reply,
)


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True)
class TelegramQueryListTests(TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 6, 12, tzinfo=ZoneInfo("America/Bogota"))
        self.today = patch("mainApp.services.telegram_bot.timezone.localdate", return_value=self.now.date())
        self.today_mock = self.today.start()
        self.addCleanup(self.today.stop)
        role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("William prueba", rolid=role)
        self.profile = TelegramUsuario.objects.create(
            usuario=self.user, telegram_user_id=9201, telegram_chat_id=9201,
        )
        self.client_stub = SimpleNamespace(answer_callback=MagicMock())
        self.branch = Sucursal.objects.create(nombre="Yerbabuena")

    def expense(self, name="COCA-COLA", amount="1000", method="efectivo", author="William prueba", at=None):
        concept, _ = ConceptoEgreso.objects.get_or_create(nombre=name)
        expense = Egreso.objects.create(
            concepto=concept, monto=Decimal(amount), medio_pago=method,
            registrado_por_nombre=author,
        )
        Egreso.objects.filter(pk=expense.pk).update(creado_en=at or self.now)
        return expense

    def employee(self, name="Ana", surname="Pérez", position="Cajera", branch=None):
        index = Empleado.objects.count() + 1
        # La consulta no necesita ejecutar el flujo de creación de clientes.
        employee = Empleado(
            nombre=name, apellido=surname, puesto=position,
            sucursalid=branch or self.branch,
            telefono=f"300000{index}", email=f"privado{index}@test.invalid",
            direccion=f"DIRECCION PRIVADA {index}", numerodocumento=f"DOCUMENTO PRIVADO {index}",
        )
        Empleado.objects.bulk_create([employee])
        return employee

    def query(self, tool="consultar_pagos", **arguments):
        return _execute_tool(self.profile, tool, arguments)

    def navigate(self, reply, label="Siguiente", profile=None):
        button = next(
            button for row in reply.reply_markup["inline_keyboard"] for button in row
            if label in button["text"]
        )
        update = SimpleNamespace(texto=button["callback_data"], callback_query_id="list-test")
        return _handle_callback(update, profile or self.profile, self.client_stub)

    def test_payments_show_real_details_author_date_and_exact_amount(self):
        row = self.expense(amount="1234.50", method="nequi", author="Nombre histórico")
        reply = self.query()
        for expected in (f"#{row.pk}", "COCA-COLA", "$1.234,50", "Nequi", "06/09/2026 12:00", "Nombre histórico"):
            self.assertIn(expected, reply.text)
        self.assertIn("Total pagado: $1.234,50", reply.text)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_today_uses_colombian_date_at_utc_midnight_boundaries(self):
        included = self.expense(name="NOCHE DE HOY", at=datetime(2026, 9, 7, 4, 30, tzinfo=utc_timezone.utc))
        self.expense(name="NOCHE DE AYER", at=datetime(2026, 9, 6, 4, 30, tzinfo=utc_timezone.utc))
        reply = self.query()
        self.assertIn(f"#{included.pk}", reply.text)
        self.assertNotIn("NOCHE DE AYER", reply.text)
        self.assertIn("1 registro(s)", reply.text)

    def test_payments_combine_date_concept_method_author_and_amount_filters(self):
        expected = self.expense(amount="50000", method="nequi")
        self.expense(name="AGUA", amount="50000", method="nequi")
        self.expense(amount="60000", method="nequi", author="Otra persona")
        self.expense(amount="10", method="nequi")
        self.expense(amount="60000", method="efectivo")
        reply = self.query(
            desde="2026-09-06", hasta="2026-09-06", concepto="COCA",
            usuario="William", medio_pago="nequi", monto_min=50000, monto_max=55000,
        )
        self.assertEqual(re.findall(r"• #(\d+)", reply.text), [str(expected.pk)])
        self.assertIn("Total pagado: $50.000", reply.text)

    def test_legacy_card_methods_are_combined_in_filter_and_summary(self):
        self.expense(method="tarjeta")
        self.expense(method="caja_social", amount="2000")
        reply = self.query(medio_pago="Banco Caja Social")
        self.assertIn("2 registro(s)", reply.text)
        self.assertIn("Total pagado: $3.000", reply.text)

    def test_summary_and_empty_list_are_explicit(self):
        empty = self.query()
        self.assertIn("No hay pagos", empty.text)
        self.expense()
        summary = self.query(detalle=False)
        self.assertIn("Total pagado", summary.text)
        self.assertNotIn("Detalle", summary.text)
        self.assertIsNone(summary.reply_markup)

    def test_payment_pages_include_all_records_and_keep_total_for_full_interval(self):
        ids = {str(self.expense(name=f"PAGO {i}", amount="1000").pk) for i in range(6)}
        first = self.query()
        second = self.navigate(first)
        first_ids = set(re.findall(r"• #(\d+)", first.text))
        second_ids = set(re.findall(r"• #(\d+)", second.text))
        self.assertEqual(len(first_ids), 5)
        self.assertEqual(len(second_ids), 1)
        self.assertFalse(first_ids & second_ids)
        self.assertEqual(first_ids | second_ids, ids)
        self.assertIn("Total pagado: $6.000", first.text)
        self.assertIn("Total pagado: $6.000", second.text)
        self.assertIn("página 2 de 2", second.text)
        back = self.navigate(second, "Anterior")
        self.assertEqual(set(re.findall(r"• #(\d+)", back.text)), first_ids)

    def test_pagination_preserves_filter_dates_across_midnight(self):
        for i in range(6):
            self.expense(name=f"PAGO {i}", method="nequi")
        self.expense(name="OTRO MEDIO")
        first = self.query(medio_pago="nequi")
        self.today_mock.return_value = (self.now + timedelta(days=1)).date()
        second = self.navigate(first)
        self.assertIn("06/09/2026", second.text)
        self.assertIn("Total pagado: $6.000", second.text)
        self.assertNotIn("OTRO MEDIO", second.text)

    def test_employee_list_shows_job_and_branch_but_not_sensitive_contact_fields(self):
        row = self.employee()
        reply = self.query("listar_empleados")
        for expected in (f"ID {row.pk}", "Ana Pérez", "Cajera", "Yerbabuena", "Sin usuario vinculado"):
            self.assertIn(expected, reply.text)
        for private in (row.telefono, row.email, row.direccion, row.numerodocumento):
            self.assertNotIn(private, reply.text)

    def test_employees_can_be_filtered_by_full_name_job_and_branch(self):
        target = self.employee()
        self.employee(name="Ana", surname="Gómez", position="Bodega")
        other_branch = Sucursal.objects.create(nombre="Otra sucursal")
        self.employee(branch=other_branch)
        reply = self.query("listar_empleados", consulta="Ana Pérez", cargo="Cajera", sucursal="Yerbabuena")
        self.assertIn("Empleados encontrados: 1.", reply.text)
        self.assertIn(f"ID {target.pk}", reply.text)

    def test_employee_pagination_and_empty_search(self):
        for index in range(6):
            self.employee(name=f"Persona {index}")
        first = self.query("listar_empleados")
        second = self.navigate(first)
        self.assertIn("Página 2 de 2", second.text)
        self.assertIn("Persona 5", second.text)
        empty = self.query("listar_empleados", consulta="No existe")
        self.assertIn("No hay empleados", empty.text)

    def test_employee_permissions_and_inactive_accounts_are_enforced(self):
        self.employee()
        with patch("mainApp.services.telegram_bot.user_can_access_url_name", return_value=False):
            with self.assertRaises(PermissionDenied):
                self.query("listar_empleados")
        self.user.is_active = False
        with self.assertRaises(PermissionDenied):
            self.query()

    def test_pages_cannot_be_opened_by_another_user_or_after_permission_revocation(self):
        for index in range(6):
            self.employee(name=f"Empleado {index}")
        first = self.query("listar_empleados")
        other_user = Usuario.objects.create_user("Otra cuenta")
        other = TelegramUsuario.objects.create(usuario=other_user, telegram_user_id=9202, telegram_chat_id=9202)
        denied = self.navigate(first, profile=other)
        self.assertEqual(denied.intent, "pagina_invalida")
        self.assertNotIn("Empleado", denied.text)
        with patch("mainApp.services.telegram_bot.user_can_access_url_name", return_value=False):
            with self.assertRaises(PermissionDenied):
                self.navigate(first)

    def test_expired_pagination_cannot_be_reused(self):
        for i in range(6):
            self.expense(name=f"PAGO {i}")
        first = self.query()
        TelegramAuditoria.objects.update(creado_en=self.now - timedelta(days=1000))
        self.assertEqual(self.navigate(first).intent, "pagina_invalida")

    def test_page_callback_cannot_execute_payment_preparation(self):
        audit = TelegramAuditoria.objects.create(
            usuario=self.user, telegram_user_id=self.profile.telegram_user_id,
            accion="preparar_registro_pago", argumentos={"concepto": "EVITAR PAGO", "monto": 1000},
        )
        update = SimpleNamespace(texto=f"page:{audit.pk}:1", callback_query_id="not-a-list")
        reply = _handle_callback(update, self.profile, self.client_stub)
        self.assertEqual(reply.intent, "pagina_invalida")
        self.assertFalse(Egreso.objects.exists())
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_invalid_dates_pages_and_amount_filters_are_rejected(self):
        for arguments in (
            {"desde": "2026-99-01"}, {"desde": "no es fecha"},
            {"desde": "2026-09-06", "hasta": "2026-09-05"},
            {"pagina": -1}, {"pagina": 2}, {"pagina": 1.5},
            {"monto_min": "NaN"}, {"monto_max": "Infinity"},
            {"monto_min": 100, "monto_max": 1},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(TelegramBotError):
                self.query(**arguments)

    def test_list_messages_fit_without_truncating_records(self):
        for index in range(5):
            self.expense(name=f"{index}" + "C" * 159, amount="999999999999.99", author="U" * 160)
            self.employee(name="N" * 100, surname="A" * 100)
        for tool in ("consultar_pagos", "listar_empleados"):
            reply = self.query(tool)
            self.assertLessEqual(len(reply.text), 4000)

    def test_both_ai_providers_expose_employee_tool_and_rich_payment_filters(self):
        gemini = {tool["name"]: tool for tool in GEMINI_TOOLS[0]["functionDeclarations"]}
        groq = {tool["function"]["name"]: tool["function"] for tool in GROQ_CHAT_TOOLS}
        for tools in (gemini, groq):
            self.assertIn("listar_empleados", tools)
            self.assertIn("usuario", tools["consultar_pagos"]["parameters"]["properties"])
            self.assertIn("detalle", tools["consultar_pagos"]["parameters"]["properties"])
        self.assertEqual(_validated_ai_call("listar_empleados", {})[0], "listar_empleados")

    def test_slash_commands_need_no_ai_and_natural_voice_queries_are_read_only(self):
        self.expense()
        self.employee()
        for index, (kind, text, tool) in enumerate((
            ("TEXTO", "/pagos", "consultar_pagos"),
            ("TEXTO", "/empleados Ana", "listar_empleados"),
            ("VOZ", "Muéstrame los pagos de hoy", "consultar_pagos"),
            ("TEXTO", "Dame la lista de empleados", "listar_empleados"),
        ), start=92001):
            update = TelegramActualizacion.objects.create(
                update_id=index, telegram_user_id=9201, telegram_chat_id=9201,
                chat_type="private", tipo=kind, texto=text if kind == "TEXTO" else "",
                transcripcion=text if kind == "VOZ" else "",
            )
            with patch("mainApp.services.telegram_bot._intelligent_function_call", return_value=(tool, {}, "")) as ai:
                reply = build_reply(update, self.client_stub)
            self.assertEqual(reply.intent, tool)
            if text.startswith("/"):
                ai.assert_not_called()
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertFalse(TelegramAccionPendiente.objects.exists())
