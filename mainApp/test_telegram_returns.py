from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection
from django.test import TestCase, override_settings
from django.utils import timezone

from mainApp.models import (
    CambioDevolucion, Categoria, ConfiguracionFuncionalidad, DetalleVenta,
    Empleado, Inventario, MetodoPago, PagoVenta, Permiso, Producto, PuntosPago,
    ReintegroVenta, Rol, RolPermiso, Sucursal, TelegramAccionPendiente, TelegramActualizacion,
    TelegramAuditoria, TelegramUsuario, TurnoCaja, TurnoCajaMedio, Usuario,
    UsuarioPermiso, Venta,
)
from mainApp.permissions import clear_permission_cache, user_can_change_sale
from mainApp.services import telegram_bot as bot
from mainApp.services import telegram_returns as returns
from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE
from mainApp.views import VentaDetailView


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True)
class TelegramReturnTests(TestCase):
    @classmethod
    def setUpClass(cls):
        # Tabla heredada managed=False: no la crea syncdb en SQLite de pruebas.
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
        role = Rol.objects.create(nombre="Web Master")
        # test_settings no ejecuta las migraciones que asignan permisos al rol.
        permission = Permiso.objects.create(nombre="ventas_cambios")
        RolPermiso.objects.create(rol=role, permiso=permission)
        self.user = Usuario.objects.create_user("Operador devolución", rolid=role)
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=8401, telegram_chat_id=8401)
        self.client_stub = SimpleNamespace(answer_callback=MagicMock())
        self.branch = Sucursal.objects.create(nombre="Yerbabuena")
        self.point = PuntosPago.objects.create(nombre="Caja principal", sucursalid=self.branch, dinerocaja=10000)
        self.category = Categoria.objects.create(nombre="ALIMENTOS")
        self.product = Producto.objects.create(nombre="ARROZ", categoria=self.category, precio=500)
        self.inventory = Inventario.objects.create(productoid=self.product, sucursalid=self.branch, cantidad=6)
        employee = Empleado(nombre="Ana", apellido="Prueba", telefono="3001112233", email="ana@example.test", numerodocumento="11001", puesto="Cajera", sucursalid=self.branch)
        Empleado.objects.bulk_create([employee])
        self.sale = Venta.objects.create(fecha=timezone.localdate() - timedelta(days=1), hora="12:00", empleadoid=employee, sucursalid=self.branch, puntopagoid=self.point, total=Decimal("2000.00"), mediopago="nequi")
        self.detail = DetalleVenta.objects.create(ventaid=self.sale, productoid=self.product, cantidad=4, preciounitario=500)
        PagoVenta.objects.create(ventaid=self.sale, medio_pago="nequi", monto=2000)
        self.shift = TurnoCaja.objects.create(puntopago=self.point, cajero=self.user, estado="ABIERTO", ventas_total=0, ventas_efectivo=0)
        self.feature = ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=True)
        for code, label in (("efectivo", "Efectivo"), ("nequi", "Nequi"), ("tarjeta", "Tarjeta / Banco Caja Social")):
            MetodoPago.objects.create(codigo=code, nombre=label, activo=True, es_efectivo=code == "efectivo")

    def prepare(self, quantity=1, method=None, products=None, sale_id=None, profile=None):
        args = {"venta_id": self.sale.pk if sale_id is None else sale_id, "productos": products if products is not None else [{"producto_id": self.product.pk, "cantidad": quantity}]}
        if method is not None:
            args["medio_pago"] = method
        return bot._execute_tool(profile or self.profile, "preparar_devolucion_venta", args)

    def callback(self, reply, label="Confirmar", profile=None):
        button = next(button for row in reply.reply_markup["inline_keyboard"] for button in row if label in button["text"])
        update = SimpleNamespace(texto=button["callback_data"], callback_query_id="refund-test")
        return bot._handle_callback(update, profile or self.profile, self.client_stub)

    def assert_unchanged(self):
        self.detail.refresh_from_db()
        self.sale.refresh_from_db()
        self.inventory.refresh_from_db()
        self.shift.refresh_from_db()
        self.point.refresh_from_db()
        self.assertEqual(self.detail.cantidad, 4)
        self.assertEqual(self.sale.total, Decimal("2000"))
        self.assertEqual(self.inventory.cantidad, 6)
        self.assertEqual(self.shift.ventas_total, 0)
        self.assertEqual(self.point.dinerocaja, 10000)
        self.assertFalse(CambioDevolucion.objects.exists())
        self.assertFalse(ReintegroVenta.objects.exists())

    def test_preparation_is_read_only_and_defaults_to_cash_not_original_nequi(self):
        reply = self.prepare(quantity=2)
        self.assert_unchanged()
        self.assertIn("$1.000", reply.text)
        self.assertIn("Medio: Efectivo", reply.text)
        self.assertIn(f"turno actual #{self.shift.pk}", reply.text)
        self.assertIn("NO envía dinero", reply.text)
        self.assertIn("Todavía no se ha guardado", reply.text)
        self.assertEqual(TelegramAccionPendiente.objects.get().argumentos["medio_pago"], "efectivo")

    def test_confirm_updates_stock_sale_refund_and_current_shift_preserving_payment(self):
        response = self.callback(self.prepare(quantity=2))
        self.assertIn("Devolución registrada", response.text)
        self.sale.refresh_from_db()
        self.detail.refresh_from_db()
        self.inventory.refresh_from_db()
        self.shift.refresh_from_db()
        self.assertEqual(self.sale.total, 1000)
        self.assertEqual(self.sale.mediopago, "nequi")
        self.assertEqual(PagoVenta.objects.get(ventaid=self.sale).monto, 2000)
        self.assertEqual(self.detail.cantidad, 2)
        self.assertEqual(self.inventory.cantidad, 8)
        refund = ReintegroVenta.objects.get()
        self.assertEqual((refund.monto, refund.medio_pago, refund.turno_id, refund.registrado_por_id), (1000, "efectivo", self.shift.pk, self.user.pk))
        self.assertEqual(CambioDevolucion.objects.get().cantidad, 2)
        self.assertEqual(self.shift.ventas_total, -1000)
        self.assertEqual(self.shift.ventas_efectivo, -1000)
        self.assertEqual(TurnoCajaMedio.objects.get(turno=self.shift, metodo="efectivo").esperado, -1000)
        audit = TelegramAuditoria.objects.get(accion="confirmar_devolucion_venta")
        self.assertTrue(audit.exitoso)
        self.assertEqual(audit.usuario_id, self.user.pk)

    def test_confirming_twice_cannot_return_or_reimburse_twice(self):
        reply = self.prepare()
        self.callback(reply)
        self.assertIn("ya estaba", self.callback(reply).text)
        self.assertEqual(ReintegroVenta.objects.count(), 1)
        self.assertEqual(CambioDevolucion.objects.count(), 1)
        self.detail.refresh_from_db()
        self.assertEqual(self.detail.cantidad, 3)

    def test_different_current_cashier_receives_refund_not_closed_historical_shift(self):
        historical = self.shift
        historical.estado = "CERRADO"
        historical.ventas_total = 2000
        historical.save(update_fields=["estado", "ventas_total"])
        other_cashier = Usuario.objects.create_user("Nuevo cajero")
        active = TurnoCaja.objects.create(puntopago=self.point, cajero=other_cashier, estado="ABIERTO")
        reply = self.prepare()
        self.assertIn("Nuevo cajero", reply.text)
        self.callback(reply)
        self.assertEqual(ReintegroVenta.objects.get().turno_id, active.pk)
        historical.refresh_from_db()
        self.assertEqual(historical.ventas_total, 2000)

    def test_specified_nequi_and_card_alias_are_respected(self):
        self.callback(self.prepare(method="nequi"))
        refund = ReintegroVenta.objects.latest("pk")
        self.assertEqual(refund.medio_pago, "nequi")
        self.assertEqual(TurnoCajaMedio.objects.get(turno=self.shift, metodo="nequi").esperado, -500)
        self.callback(self.prepare(method="Banco Caja Social"))
        self.assertEqual(ReintegroVenta.objects.latest("pk").medio_pago, "tarjeta")

    def test_active_custom_payment_method_is_supported(self):
        MetodoPago.objects.create(codigo="transferencia", nombre="Transferencia", activo=True)
        self.callback(self.prepare(method="transferencia"))
        self.assertEqual(ReintegroVenta.objects.get().medio_pago, "transferencia")

    def test_disabled_unknown_and_mixed_methods_are_rejected_without_proposal(self):
        MetodoPago.objects.filter(codigo="nequi").update(activo=False)
        for method in ("nequi", "otro inventado", "mixto", "sin_pago", "facturas_pagadas", ""):
            with self.subTest(method=method), self.assertRaises(bot.TelegramBotError):
                self.prepare(method=method)
        self.assertFalse(TelegramAccionPendiente.objects.exists())
        self.assert_unchanged()

    def test_zero_cost_return_restores_inventory_without_cash_or_turn(self):
        self.sale.total = 0
        self.sale.mediopago = "sin_pago"
        self.sale.save(update_fields=["total", "mediopago"])
        PagoVenta.objects.all().delete()
        self.shift.estado = "CERRADO"
        self.shift.save(update_fields=["estado"])
        reply = self.prepare()
        self.assertIn("no entrega dinero", reply.text)
        self.callback(reply)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.cantidad, 7)
        self.assertFalse(ReintegroVenta.objects.exists())
        self.assertTrue(CambioDevolucion.objects.exists())

    def test_partial_and_complete_returns_prorate_discount_without_over_refunding(self):
        self.sale.total = Decimal("1000")
        self.sale.save(update_fields=["total"])
        PagoVenta.objects.filter(ventaid=self.sale).update(monto=1000)
        reply = self.prepare()
        self.assertIn("Total a reintegrar: $250", reply.text)
        self.callback(reply)
        self.callback(self.prepare(quantity=3))
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.total, 0)
        self.assertEqual(sum(ReintegroVenta.objects.values_list("monto", flat=True)), 1000)
        with self.assertRaises(bot.TelegramBotError):
            self.prepare()

    def test_without_turn_control_cash_adjusts_payment_point_balance(self):
        self.feature.habilitada = False
        self.feature.save(update_fields=["habilitada"])
        reply = self.prepare()
        self.assertIn("control de turnos está desactivado", reply.text)
        self.callback(reply)
        refund = ReintegroVenta.objects.get()
        self.assertIsNone(refund.turno_id)
        self.point.refresh_from_db()
        self.shift.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, 9500)
        self.assertEqual(self.shift.ventas_total, 0)

    def test_no_active_turn_blocks_paid_return_before_proposal(self):
        self.shift.estado = "CERRADO"
        self.shift.save(update_fields=["estado"])
        with self.assertRaisesMessage(bot.TelegramBotError, "No hay un turno activo"):
            self.prepare()
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_invalid_and_excess_quantities_and_unknown_sale(self):
        for quantity in (0, -1, 5, 1.5, "1", True, 99999999999999):
            with self.subTest(quantity=quantity), self.assertRaises(bot.TelegramBotError):
                self.prepare(quantity=quantity)
        with self.assertRaises(bot.TelegramBotError):
            self.prepare(sale_id=9999999)
        self.assert_unchanged()

    def test_cannot_return_foreign_or_duplicate_lines(self):
        other = Producto.objects.create(nombre="NO COMPRADO", precio=1000, categoria=self.category)
        for products in (
            [{"producto_id": other.pk, "cantidad": 1}],
            [{"detalle_id": 9999999, "cantidad": 1}],
            [{"producto_id": self.product.pk, "cantidad": 1}, {"detalle_id": self.detail.pk, "cantidad": 1}],
            [{"producto_id": self.product.pk, "detalle_id": self.detail.pk, "cantidad": 1}],
            [{"cantidad": 1}], [],
        ):
            with self.subTest(products=products), self.assertRaises(bot.TelegramBotError):
                self.prepare(products=products)
        self.assert_unchanged()

    def test_exact_name_selects_but_ambiguous_product_requires_detail_id(self):
        reply = self.prepare(products=[{"producto": "arroz", "cantidad": 1}])
        self.assertIn(f"detalle #{self.detail.pk}", reply.text)
        duplicate = DetalleVenta.objects.create(ventaid=self.sale, productoid=self.product, cantidad=1, preciounitario=400)
        with self.assertRaisesMessage(bot.TelegramBotError, "varios renglones"):
            self.prepare()
        reply = self.prepare(products=[{"detalle_id": duplicate.pk, "cantidad": 1}])
        self.assertIn(f"detalle #{duplicate.pk}", reply.text)

    def test_multiple_products_are_returned_together_and_stock_created_if_missing(self):
        other = Producto.objects.create(nombre="ACEITE", categoria=self.category, precio=1000)
        DetalleVenta.objects.create(ventaid=self.sale, productoid=other, cantidad=2, preciounitario=1000)
        self.sale.total = 4000
        self.sale.save(update_fields=["total"])
        PagoVenta.objects.filter(ventaid=self.sale).update(monto=4000)
        reply = self.prepare(products=[{"producto_id": self.product.pk, "cantidad": 1}, {"producto_id": other.pk, "cantidad": 2}])
        self.assertIn("$2.500", reply.text)
        self.callback(reply)
        self.assertEqual(CambioDevolucion.objects.count(), 2)
        self.assertEqual(Inventario.objects.get(productoid=other, sucursalid=self.branch).cantidad, 2)
        self.assertEqual(ReintegroVenta.objects.get().monto, 2500)

    def test_changes_to_sale_or_other_line_invalidate_confirmation(self):
        reply = self.prepare()
        self.sale.total = 1800
        self.sale.save(update_fields=["total"])
        result = self.callback(reply)
        self.assertIn("No se registró", result.text)
        self.assertFalse(ReintegroVenta.objects.exists())
        self.assertFalse(CambioDevolucion.objects.exists())

    def test_two_proposals_cannot_overwrite_each_others_returns(self):
        first, second = self.prepare(), self.prepare()
        self.callback(first)
        self.assertIn("cambiaron", self.callback(second).text)
        self.assertEqual(ReintegroVenta.objects.count(), 1)
        self.assertEqual(TelegramAccionPendiente.objects.filter(estado="ERROR").count(), 1)

    def test_changed_turn_does_not_silently_charge_new_cashier(self):
        reply = self.prepare()
        self.shift.estado = "CERRADO"
        self.shift.save(update_fields=["estado"])
        TurnoCaja.objects.create(puntopago=self.point, cajero=self.user, estado="ABIERTO")
        result = self.callback(reply)
        self.assertIn("cambiaron", result.text)
        self.assert_unchanged()

    def test_disabled_payment_or_changed_feature_prevents_confirmation(self):
        reply = self.prepare(method="nequi")
        MetodoPago.objects.filter(codigo="nequi").update(activo=False)
        self.assertIn("No se registró", self.callback(reply).text)
        reply = self.prepare()
        ConfiguracionFuncionalidad.objects.filter(pk=TURN_REQUIRED_FEATURE).update(habilitada=False)
        self.assertIn("cambiaron", self.callback(reply).text)
        self.assert_unchanged()

    def test_cancel_expiry_and_foreign_buttons_do_not_apply_returns(self):
        reply = self.prepare()
        other = Usuario.objects.create_user("Otra cuenta", rolid=self.user.rolid)
        foreign = TelegramUsuario.objects.create(usuario=other, telegram_user_id=8402, telegram_chat_id=8402)
        self.assertIn("otra cuenta", self.callback(reply, profile=foreign).text)
        self.callback(reply, "Cancelar")
        self.assertIn("ya estaba", self.callback(reply).text)
        self.assertTrue(TelegramAuditoria.objects.filter(accion="cancelar_devolucion_venta").exists())
        reply = self.prepare()
        TelegramAccionPendiente.objects.filter(estado="PENDIENTE").update(vence_en=timezone.now() - timedelta(seconds=1))
        self.assertIn("venció", self.callback(reply).text)
        self.assert_unchanged()

    def test_web_and_bot_require_change_permission_and_exclude_cashier(self):
        change_permission = Permiso.objects.get(nombre="ventas_cambios")
        for role_name, grant, expected in (("Solo lectura", False, False), ("Cajero", True, False), ("Supervisor", True, True)):
            role = Rol.objects.create(nombre=role_name)
            user = Usuario.objects.create_user(role_name, rolid=role)
            if grant:
                UsuarioPermiso.objects.create(usuario=user, permiso=change_permission, permitido=True)
            clear_permission_cache(user)
            self.assertEqual(user_can_change_sale(user), expected)
            self.assertEqual(VentaDetailView()._can_edit_venta(user), expected)
            profile = TelegramUsuario.objects.create(usuario=user, telegram_user_id=9000 + user.pk, telegram_chat_id=9000 + user.pk)
            if expected:
                self.assertIn("Propuesta de devolución", self.prepare(profile=profile).text)
            else:
                with self.assertRaises(PermissionDenied):
                    self.prepare(profile=profile)

    def test_permission_revoked_or_user_disabled_after_proposal_is_rechecked(self):
        reply = self.prepare()
        Usuario.objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.callback(reply)
        self.assert_unchanged()

    def test_failure_after_stock_updates_rolls_back_all_business_changes(self):
        reply = self.prepare()
        with patch.object(CambioDevolucion, "_upsert_turno_medio_delta", side_effect=IntegrityError("SECRET SQL DETAIL")):
            result = self.callback(reply)
        self.assertIn("No se registró", result.text)
        self.assertNotIn("SECRET SQL", result.text)
        self.assert_unchanged()
        self.assertEqual(TelegramAccionPendiente.objects.get().estado, "ERROR")
        self.assertFalse(TelegramAuditoria.objects.get(accion="confirmar_devolucion_venta").exitoso)

    def test_missing_ledger_blocks_without_writing(self):
        with patch.object(returns.connection.introspection, "table_names", return_value=[]):
            with self.assertRaisesMessage(bot.TelegramBotError, "0021"):
                self.prepare()
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_schema_registered_for_both_providers_and_does_not_accept_amount(self):
        self.assertIn("preparar_devolucion_venta", {item["name"] for item in bot.GEMINI_TOOLS[0]["functionDeclarations"]})
        self.assertIn("preparar_devolucion_venta", {item["function"]["name"] for item in bot.GROQ_CHAT_TOOLS})
        with self.assertRaises(bot.TelegramBotError):
            bot._execute_tool(self.profile, "preparar_devolucion_venta", {"venta_id": self.sale.pk, "productos": [{"producto_id": self.product.pk, "cantidad": 1}], "monto": 999999})

    def test_command_bypasses_ai_and_voice_uses_same_confirmation(self):
        with patch.object(bot, "_intelligent_function_call") as ai:
            reply = bot._handle_command(None, self.profile, f"/devolver {self.sale.pk} {self.product.pk}:1 Banco Caja Social")
            self.assertIn("Tarjeta / Banco Caja Social", reply.text)
            ai.assert_not_called()
        update = TelegramActualizacion.objects.create(update_id=84001, telegram_user_id=8401, telegram_chat_id=8401, tipo="VOZ", transcripcion=f"Devuelve uno de arroz de la venta {self.sale.pk}")
        with patch.object(bot, "_intelligent_function_call", return_value=("preparar_devolucion_venta", {"venta_id": self.sale.pk, "productos": [{"producto": "ARROZ", "cantidad": 1}]}, "")):
            reply = bot.build_reply(update, self.client_stub)
        self.assertIn("Confirmar devolución", str(reply.reply_markup))
        self.assertTrue(TelegramAccionPendiente.objects.filter(actualizacion=update).exists())
        self.assert_unchanged()

    def test_too_many_lines_are_rejected_without_silently_truncating(self):
        with self.assertRaises(bot.TelegramBotError):
            self.prepare(products=[{"producto_id": self.product.pk, "cantidad": 1}] * 21)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_nonconfirm_button_cannot_apply_return(self):
        reply = self.prepare()
        pending = TelegramAccionPendiente.objects.get()
        update = SimpleNamespace(texto=f"newconcept:{pending.pk}", callback_query_id="wrong-kind")
        self.assertIn("Confirmar devolución", bot._handle_callback(update, self.profile, self.client_stub).text)
        self.assert_unchanged()

    def test_legacy_single_method_sale_materializes_original_income_only_on_confirm(self):
        PagoVenta.objects.filter(ventaid=self.sale).delete()
        reply = self.prepare()
        self.assertFalse(PagoVenta.objects.exists())
        self.callback(reply)
        payment = PagoVenta.objects.get(ventaid=self.sale)
        self.assertEqual((payment.medio_pago, payment.monto), ("nequi", Decimal("2000")))
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.total, 1500)
        self.assertEqual(ReintegroVenta.objects.get().monto, 500)

    def test_legacy_original_income_includes_previous_refunds(self):
        self.callback(self.prepare())
        PagoVenta.objects.filter(ventaid=self.sale).delete()
        self.callback(self.prepare())
        self.assertEqual(PagoVenta.objects.get(ventaid=self.sale).monto, 2000)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.total, 1000)

    def test_mixed_payment_sale_keeps_exact_original_distribution(self):
        self.sale.mediopago = "mixto"
        self.sale.save(update_fields=["mediopago"])
        PagoVenta.objects.filter(ventaid=self.sale).update(monto=1500)
        PagoVenta.objects.create(ventaid=self.sale, medio_pago="tarjeta", monto=500)
        before = list(PagoVenta.objects.order_by("pk").values_list("pk", "medio_pago", "monto"))
        self.callback(self.prepare(method="efectivo"))
        self.assertEqual(list(PagoVenta.objects.order_by("pk").values_list("pk", "medio_pago", "monto")), before)

    def test_inconsistent_or_missing_mixed_payments_are_not_invented(self):
        PagoVenta.objects.filter(ventaid=self.sale).update(monto=1500)
        with self.assertRaisesMessage(bot.TelegramBotError, "pagos históricos"):
            self.prepare()
        PagoVenta.objects.filter(ventaid=self.sale).delete()
        self.sale.mediopago = "mixto"
        self.sale.save(update_fields=["mediopago"])
        with self.assertRaisesMessage(bot.TelegramBotError, "distribución de pagos"):
            self.prepare()
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_changed_original_payment_distribution_invalidates_confirmation(self):
        reply = self.prepare()
        PagoVenta.objects.filter(ventaid=self.sale).update(medio_pago="tarjeta")
        self.assertIn("cambiaron", self.callback(reply).text)
        self.assert_unchanged()
