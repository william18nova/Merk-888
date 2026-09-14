from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection
from django.test import TestCase
from django.utils import timezone

from .models import (CambioEgreso, Categoria, ConceptoEgreso, ConteoCierrePTM, DetalleVenta,
                     Egreso, Empleado, MetodoPago, OperacionPTM, PagoVenta, Producto, PuntosPago,
                     NotificacionNequi, Rol, RolPermiso, Sucursal, TelegramAccionPendiente, TelegramActualizacion,
                     TelegramUsuario, TelegramAuditoria, TurnoCaja, Usuario, Venta)
from .services import telegram_bot as bot
from .services.telegram_ai_policy import selected_tool_names, compact_prompt
from .services.telegram_operations import validate_arguments
from .services.telegram_shortcuts import specific_read_request


class ExpandedBotTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.created_role_permissions = RolPermiso._meta.db_table not in connection.introspection.table_names()
        if cls.created_role_permissions:
            with connection.schema_editor() as editor:
                editor.create_model(RolPermiso)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        if cls.created_role_permissions:
            with connection.schema_editor() as editor:
                editor.delete_model(RolPermiso)

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = Usuario.objects.create_user("William prueba", rolid=Rol.objects.create(nombre="Web Master"))
        self.cashier = Usuario.objects.create_user("Camila prueba", rolid=Rol.objects.create(nombre="Cajero"))
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=77401, telegram_chat_id=77401)
        self.other_profile = TelegramUsuario.objects.create(usuario=self.cashier, telegram_user_id=77402, telegram_chat_id=77402)
        self.client_stub = SimpleNamespace(answer_callback=MagicMock())
        self.branch = Sucursal.objects.create(nombre="Yerbabuena")
        self.point = PuntosPago.objects.create(nombre="Caja principal", sucursalid=self.branch)
        self.turn = TurnoCaja.objects.create(cajero=self.cashier, puntopago=self.point)
        self.category = Categoria.objects.create(nombre="BEBIDAS")
        self.product = Producto.objects.create(nombre="AGUA NATURAL", categoria=self.category, precio=Decimal("1500.25"), codigo_de_barras="0000123")
        employee = Empleado(nombre="Camila", apellido="Prueba", usuarioid=self.cashier, sucursalid=self.branch, puesto="Cajero", numerodocumento="BOT-NEW", email="new@example.test")
        Empleado.objects.bulk_create([employee])
        self.sale = Venta.objects.create(empleadoid=employee, sucursalid=self.branch, puntopagoid=self.point, fecha=timezone.localdate(), hora="12:00", total=3000.50, mediopago="nequi")
        DetalleVenta.objects.create(ventaid=self.sale, productoid=self.product, cantidad=2, preciounitario=1500.25)
        PagoVenta.objects.create(ventaid=self.sale, medio_pago="nequi", monto=3000.50)
        for code, name in (("efectivo", "Efectivo"), ("nequi", "Nequi")):
            MetodoPago.objects.create(codigo=code, nombre=name, activo=True, es_efectivo=code == "efectivo")
        self.concept = ConceptoEgreso.objects.create(nombre="COCA-COLA")
        self.expense = Egreso.objects.create(concepto=self.concept, monto=Decimal("1000.25"), medio_pago="efectivo", registrado_por=self.cashier, registrado_por_nombre="Camila prueba")
        Egreso.objects.filter(pk=self.expense.pk).update(creado_en=timezone.now() - timedelta(days=60))
        self.expense.refresh_from_db()

    def message(self, text, voice=False):
        return TelegramActualizacion.objects.create(
            update_id=90000 + TelegramActualizacion.objects.count(), telegram_user_id=self.profile.telegram_user_id,
            telegram_chat_id=self.profile.telegram_chat_id, tipo="VOZ" if voice else "TEXTO",
            texto="" if voice else text, transcripcion=text if voice else "",
        )

    def query(self, name, **arguments):
        return bot._execute_tool(self.profile, name, arguments)

    def propose(self, **overrides):
        return self.query("preparar_edicion_pago", **dict({"pago_id": self.expense.pk, "monto": 2500.75, "motivo": "Error al digitar"}, **overrides))

    def confirm(self, reply, profile=None, verb="confirm"):
        callback = next(button["callback_data"] for row in reply.reply_markup["inline_keyboard"] for button in row if button["callback_data"].startswith(verb + ":"))
        return bot._handle_callback(SimpleNamespace(texto=callback, callback_query_id="test"), profile or self.profile, self.client_stub)

    def test_payment_is_read_by_id_without_today_filter_for_text_and_audio(self):
        for voice in (False, True):
            with patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No usar IA")):
                reply = bot.build_reply(self.message(f"Muéstrame el pago {self.expense.pk}", voice), self.client_stub)
            self.assertIn("$1.000,25", reply.text)
            self.assertIn("Camila prueba", reply.text)
            self.assertIn("COCA-COLA", reply.text)

    def test_expense_proposal_only_saves_on_button_and_keeps_original_author(self):
        date = self.expense.creado_en
        reply = self.propose(medio_pago="nequi")
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.monto, Decimal("1000.25"))
        self.assertIn("$1.000,25 → $2.500,75", reply.text)
        saved = self.confirm(reply)
        self.assertIn("corregí el pago", saved.text)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.monto, Decimal("2500.75"))
        self.assertEqual(self.expense.creado_en, date)
        self.assertEqual(self.expense.registrado_por_id, self.cashier.pk)
        self.assertEqual(CambioEgreso.objects.get().usuario_id, self.user.pk)
        self.confirm(reply)
        self.assertEqual(CambioEgreso.objects.count(), 1)

    def test_payment_history_reports_only_changed_fields(self):
        self.confirm(self.propose())
        reply = self.query("consultar_pago", pago_id=self.expense.pk, historial=True)
        self.assertIn("William prueba", reply.text)
        self.assertIn("$1.000,25 → $2.500,75", reply.text)
        self.assertIn("Error al digitar", reply.text)
        self.assertNotIn("Medio:", reply.text)

    def test_other_identity_cannot_confirm_and_cancel_does_not_change_payment(self):
        reply = self.propose()
        self.assertIn("otra cuenta", self.confirm(reply, self.other_profile).text)
        self.confirm(reply, verb="cancel")
        self.assertEqual(CambioEgreso.objects.count(), 0)
        self.assertEqual(TelegramAccionPendiente.objects.get().estado, "CANCELADA")
        self.assertTrue(TelegramAuditoria.objects.filter(accion="cancelar_edicion_pago", exitoso=True).exists())

    def test_permissions_are_rechecked_on_confirm(self):
        reply = self.propose()
        with patch.object(bot, "user_can_access_url_name", return_value=False), self.assertRaises(PermissionDenied):
            self.confirm(reply)
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_stale_web_edit_and_expired_proposal_cannot_overwrite(self):
        reply = self.propose()
        Egreso.objects.filter(pk=self.expense.pk).update(monto=9000)
        self.assertIn("No guardé", self.confirm(reply).text)
        self.assertEqual(CambioEgreso.objects.count(), 0)
        self.expense.refresh_from_db()
        reply = self.propose()
        TelegramAccionPendiente.objects.filter(estado="PENDIENTE").update(vence_en=timezone.now() - timedelta(seconds=1))
        self.assertIn("pasó el tiempo", self.confirm(reply).text)
        self.assertEqual(CambioEgreso.objects.count(), 0)

    def test_audit_failure_rolls_back_expense_and_keeps_callback_transaction_usable(self):
        reply = self.propose()
        with patch("mainApp.services.expense_editing.CambioEgreso.objects.create", side_effect=IntegrityError("fake")):
            result = self.confirm(reply)
        self.assertIn("No guardé", result.text)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.monto, Decimal("1000.25"))
        self.assertEqual(TelegramAccionPendiente.objects.get().estado, "ERROR")

    def test_missing_reason_and_ambiguous_concept_ask_without_saving(self):
        with self.assertRaises(bot.TelegramClarification):
            self.propose(motivo="")
        with self.assertRaisesMessage(bot.TelegramClarification, "COCA-COLA"):
            self.propose(concepto="cocacola")
        self.assertFalse(TelegramAccionPendiente.objects.exists())
        reply = self.propose(concepto="cocacola", concepto_nuevo=True)
        self.confirm(reply)
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.concepto.nombre, "COCACOLA")

    def test_read_shortcuts_do_not_swallow_mutations_or_multiple_requests(self):
        for text in ("Cuánto cuesta agua y cámbiala a 2000", "Muéstrame el pago 1 y bórralo", "Elimina los proveedores", "Cuánto vale agua mañana", "Muéstrame proveedores de Yerbabuena"):
            with self.subTest(text=text):
                self.assertIsNone(specific_read_request(text))

    def test_price_and_sale_focus_only_return_requested_information(self):
        with patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No usar IA")):
            price = bot.build_reply(self.message("¿Cuánto cuesta agua natural?", True), self.client_stub)
            sale = bot.build_reply(self.message(f"¿Cómo pagaron la venta {self.sale.pk}?"), self.client_stub)
            total = bot.build_reply(self.message(f"Dame el total de la venta {self.sale.pk}"), self.client_stub)
        self.assertIn("$1.500,25", price.text)
        self.assertNotIn("0000123", price.text)
        self.assertNotIn("BEBIDAS", price.text)
        self.assertIn("Nequi: $3.000,50", sale.text)
        self.assertNotIn("AGUA NATURAL", sale.text)
        self.assertEqual(total.text, f"Venta #{self.sale.pk}: $3.000,50.")

    def test_columns_display_only_requested_fields_and_reject_internal_fields(self):
        response = self.query("consultar_datos", fuente="productos", columnas=["nombre", "precio"])
        self.assertIn("AGUA NATURAL", response.text)
        self.assertNotIn("0000123", response.text)
        self.assertNotIn("BEBIDAS", response.text)
        for columns in (["password"], ["nombre", "nombre"], []):
            with self.assertRaises(bot.TelegramBotError):
                self.query("consultar_datos", fuente="productos", columnas=columns)

    def test_sale_payment_queries_allow_grouping_by_payment_method(self):
        reply = self.query("consultar_datos", fuente="cobros_ventas", operacion="sumar", campo="importe", agrupar=["medio_pago"])
        self.assertIn("Nequi", reply.text)
        self.assertIn("$3.000,50", reply.text)

    def test_ptm_totals_are_directional_and_cashier_cannot_see_other_operations(self):
        OperacionPTM.objects.create(turno=self.turn, usuario=self.user, producto=self.product, tipo="recarga", monto=20000, referencia="ADMIN-PTM")
        OperacionPTM.objects.create(turno=self.turn, usuario=self.cashier, producto=self.product, tipo="retiro", monto=8000, referencia="CAJA-PTM")
        reply = self.query("consultar_datos", fuente="ptm", operacion="sumar", campo="importe", agrupar=["tipo"])
        self.assertIn("$20.000", reply.text)
        self.assertIn("$8.000", reply.text)
        self.assertIn("no saldo ni ventas", reply.text)
        with patch.object(bot, "user_can_access_url_name", side_effect=lambda user, route: route == "operaciones_ptm"):
            result = bot._execute_tool(self.other_profile, "consultar_datos", {"fuente": "ptm"})
        self.assertIn("CAJA-PTM", result.text)
        self.assertNotIn("ADMIN-PTM", result.text)
        self.assertIn("Solo tus operaciones", result.text)

    def test_ptm_discrepancies_can_be_counted_without_editing_any_turn(self):
        ConteoCierrePTM.objects.create(turno=self.turn, usuario=self.cashier, declarado=2, registrado=4)
        ConteoCierrePTM.objects.create(turno=self.turn, usuario=self.cashier, declarado=4, registrado=4)
        response = self.query("consultar_datos", fuente="conteos_ptm", operacion="contar", filtros=[{"campo": "diferencia", "operador": "distinto", "valor": "0"}])
        self.assertIn("Conteos PTM: 1", response.text)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "ABIERTO")

    def test_new_actions_are_registered_with_all_provider_schemas_and_selector(self):
        names = set(bot.TOOL_FUNCTIONS)
        selected = selected_tool_names("Corrige el pago 12 a 1000 por error de digitación", [], names)
        self.assertTrue({"preparar_edicion_pago", "consultar_pago"} <= selected)
        prompt = compact_prompt("2026-09-13", selected)
        self.assertIn("NO registrar otro pago", prompt)
        gemini = {item["name"] for item in bot.GEMINI_TOOLS[0]["functionDeclarations"]}
        groq = {item["function"]["name"] for item in bot.GROQ_CHAT_TOOLS}
        self.assertTrue({"preparar_edicion_pago", "consultar_pago"} <= gemini & groq)

    def test_error_questions_do_not_expose_internal_parameter_paths(self):
        with self.assertRaises(bot.TelegramBotError) as raised:
            validate_arguments("consultar_pago", {"pago_id": "mal"})
        self.assertIn("número del pago", str(raised.exception))
        self.assertNotIn("pago_id", str(raised.exception))
        with self.assertRaises(bot.TelegramClarification) as raised:
            validate_arguments("preparar_edicion_pago", {})
        self.assertIn("Me falta", str(raised.exception))

    def test_columns_and_payment_history_can_continue_with_saved_context(self):
        self.query("consultar_datos", fuente="productos", columnas=["nombre"])
        response = self.query("continuar_consulta", cambios={"columnas": ["precio"]})
        self.assertIn("$1.500,25", response.text)
        self.assertNotIn("AGUA NATURAL", response.text)
        self.query("consultar_pago", pago_id=self.expense.pk)
        # Una lectura sin paginación también debe conservar su ID en la auditoría.
        response = self.query("continuar_consulta", cambios={"historial": True})
        self.assertIn("no tiene correcciones", response.text)

    def test_all_query_sources_have_valid_model_paths(self):
        from .services.telegram_queries import SOURCES
        for source in SOURCES:
            with self.subTest(source=source):
                self.query("consultar_datos", fuente=source)

    def test_closed_cash_shortages_use_closing_date_and_exclude_open_turns(self):
        self.turn.estado = "CERRADO"
        self.turn.fin = timezone.now()
        self.turn.diferencia_total = Decimal("-500.50")
        self.turn.save()
        TurnoCaja.objects.create(cajero=self.user, puntopago=self.point, diferencia_total=-90000)
        reply = self.query("consultar_datos", fuente="cierres_caja", operacion="sumar", campo="diferencia", filtros=[{"campo": "diferencia", "operador": "menor", "valor": "0"}])
        self.assertIn("500,50", reply.text)
        self.assertNotIn("90.500", reply.text)
        self.assertIn("no demuestra", reply.text)

    def test_nequi_queries_never_include_outgoing_notifications(self):
        # Solo usa los campos públicos de consulta; no almacena payloads privados.
        for external_id, amount, incoming, sale in (("new-in", 3000, True, self.sale), ("new-out", 9000, False, None), ("new-free", 2000, True, None)):
            NotificacionNequi.objects.create(fingerprint=external_id, titulo="Prueba", texto="Prueba", monto=amount, es_ingreso=incoming, venta=sale)
        reply = self.query("consultar_datos", fuente="nequi", operacion="sumar", campo="importe", filtros=[{"campo": "vinculado", "operador": "igual", "valor": "no"}])
        self.assertIn("$2.000", reply.text)
        self.assertNotIn("$11.000", reply.text)
