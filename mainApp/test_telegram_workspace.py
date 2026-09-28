import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ModelState
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from mainApp.models import (
    Categoria, ConceptoEgreso, Egreso, Inventario, PedidoProveedor, PreciosProveedor,
    Producto, Proveedor, Rol, Sucursal, TelegramAccionPendiente, TelegramActualizacion,
    TelegramAlias, TelegramAuditoria, TelegramEnvioSeguimiento, TelegramSeguimiento,
    TelegramUsuario, Usuario, Venta,
)
from mainApp.services import telegram_bot as bot
from mainApp.services import telegram_followups as followups
from mainApp.services.business_operations import BusinessOperationError, create_supplier_order, update_inventory_item
from mainApp.services.telegram_ai_policy import selected_tool_names, compact_history
from mainApp.services.telegram_proposals import ensure_proposal_buttons
from mainApp.services.telegram_search import resolve_name, search_profile


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True, TELEGRAM_BOT_TOKEN="123:test-only")
class TelegramWorkspaceTests(TestCase):
    def setUp(self):
        role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("Dueño", rolid=role)
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=8001, telegram_chat_id=8001)
        self.branch = Sucursal.objects.create(nombre="Yerbabuenatest")
        self.category = Categoria.objects.create(nombre="BEBIDAS TEST")
        self.product = Producto.objects.create(nombre="AGUA TEST", precio="2000", categoria=self.category)
        self.inventory = Inventario.objects.create(sucursalid=self.branch, productoid=self.product, cantidad=10)
        self.provider = Proveedor.objects.create(nombre="PROVEEDOR TEST")
        self.price = PreciosProveedor.objects.create(productoid=self.product, proveedorid=self.provider, precio="1200")
        self.api = SimpleNamespace(answer_callback=MagicMock(), send_message=MagicMock())

    def call(self, name, **args):
        return bot._execute_tool(self.profile, name, args)

    def confirm(self, reply):
        return bot._handle_callback(SimpleNamespace(texto=f"confirm:{reply.proposal_id}", callback_query_id="test"), self.profile, self.api)

    def inventory_proposal(self, **changes):
        args = {"producto": str(self.product.pk), "sucursal": str(self.branch.pk), "cantidad": 4, "modo": "sumar", "motivo": "Conteo físico verificado"}
        return self.call("preparar_movimiento_inventario", **dict(args, **changes))

    def order_proposal(self, **changes):
        args = {"proveedor": str(self.provider.pk), "sucursal": str(self.branch.pk), "productos": [{"producto": str(self.product.pk), "cantidad": 2}]}
        return self.call("preparar_pedido_proveedor", **dict(args, **changes))

    def workflow(self, *steps):
        return self.call("resolver_tarea", pasos=list(steps))

    def step(self, name, params, **kw):
        return {"herramienta": name, "argumentos_json": json.dumps(params), **kw}

    def alias(self, text="aguita", product=None):
        return self.call("preparar_alias", entidad="producto", alias=text, registro_id=(product or self.product).pk)

    def test_inventory_only_changes_after_confirmation_and_once(self):
        reply = self.inventory_proposal()
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.cantidad, 10)
        self.assertIn("10 → 14", reply.text)
        self.confirm(reply)
        self.confirm(reply)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.cantidad, 14)
        action = TelegramAccionPendiente.objects.get(pk=reply.proposal_id)
        self.assertEqual(action.estado, "CONFIRMADA")
        self.assertEqual(TelegramAuditoria.objects.filter(accion="confirmar_operacion_negocio", exitoso=True).count(), 1)

    def test_changed_stock_rejects_stale_proposal(self):
        reply = self.inventory_proposal()
        Inventario.objects.filter(pk=self.inventory.pk).update(cantidad=11)
        answer = self.confirm(reply)
        self.assertIn("inventario cambió", answer.text)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.cantidad, 11)
        self.assertEqual(TelegramAccionPendiente.objects.get(pk=reply.proposal_id).estado, "ERROR")

    def test_stock_bounds_and_legacy_surtido_rule(self):
        with self.assertRaises(bot.TelegramBotError):
            self.inventory_proposal(cantidad=2147483647)
        self.inventory.cantidad = 10000
        self.inventory.save()
        with self.assertRaises(bot.TelegramBotError):
            self.inventory_proposal()
        reply = self.inventory_proposal(modo="fijar", cantidad=-2)
        self.confirm(reply)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.cantidad, -2)

    def test_invalid_inventory_inputs_never_create_proposal(self):
        for values in ({"cantidad": True}, {"cantidad": 1.5}, {"motivo": ""}, {"modo": "borrar"}):
            with self.subTest(values=values), self.assertRaises(bot.TelegramBotError):
                self.inventory_proposal(**values)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_revoked_permission_blocks_confirmation(self):
        reply = self.inventory_proposal()
        with patch.object(bot, "user_can_access_url_name", return_value=False), self.assertRaises(PermissionDenied):
            self.confirm(reply)
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.cantidad, 10)

    def test_order_uses_registered_price_and_has_no_stock_or_payment_effect(self):
        reply = self.order_proposal()
        self.assertFalse(PedidoProveedor.objects.exists())
        self.assertIn("$2.400", reply.text)
        self.confirm(reply)
        self.confirm(reply)
        order = PedidoProveedor.objects.get()
        self.assertEqual(order.costototal, Decimal("2400"))
        self.assertEqual(order.estado, "En espera")
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.cantidad, 10)
        self.assertFalse(Egreso.objects.exists())

    def test_order_price_change_requires_new_proposal(self):
        reply = self.order_proposal()
        self.price.precio = Decimal("1300")
        self.price.save()
        self.assertIn("Cambió un precio", self.confirm(reply).text)
        self.assertFalse(PedidoProveedor.objects.exists())

    def test_explicit_order_price_is_preserved(self):
        reply = self.order_proposal(productos=[{"producto": str(self.product.pk), "cantidad": 3, "precio_unitario": 1100}])
        self.price.precio = Decimal("1400")
        self.price.save()
        self.confirm(reply)
        self.assertEqual(PedidoProveedor.objects.get().costototal, Decimal("3300"))

    def test_order_duplicate_missing_quantity_and_invalid_price_rejected(self):
        for lines in ([{"producto": str(self.product.pk), "cantidad": 0}],
                      [{"producto": str(self.product.pk)}],
                      [{"producto": str(self.product.pk), "cantidad": 2, "precio_unitario": -10}],
                      [{"producto": str(self.product.pk), "cantidad": 1}] * 2):
            with self.subTest(lines=lines), self.assertRaises(bot.TelegramBotError):
                self.order_proposal(productos=lines)
        self.assertFalse(PedidoProveedor.objects.exists())

    def test_new_proposal_recovers_its_real_buttons(self):
        reply = self.inventory_proposal()
        reply.reply_markup = None
        update = SimpleNamespace(telegram_user_id=self.profile.telegram_user_id)
        restored = ensure_proposal_buttons(update, reply)
        self.assertEqual(restored.proposal_id, reply.proposal_id)
        self.assertTrue(restored.reply_markup["inline_keyboard"])
        self.assertIn("10 → 14", restored.text)

    def test_alias_requires_confirmation_and_is_account_scoped(self):
        reply = self.alias("mi favorita personal")
        self.assertFalse(TelegramAlias.objects.exists())
        self.confirm(reply)
        response = self.call("buscar_producto", consulta="mi favorita personal")
        self.assertIn(self.product.nombre, response.text)
        self.assertEqual(response.references, [{"entidad": "productos", "id": self.product.pk}])
        second = Usuario.objects.create_user("Otra cuenta", rolid=self.user.rolid)
        other = TelegramUsuario.objects.create(usuario=second, telegram_user_id=8002, telegram_chat_id=8002)
        self.assertIn("No encontré", bot._execute_tool(other, "buscar_producto", {"consulta": "mi favorita personal"}).text)
        self.assertIsNone(search_profile.get())

    def test_alias_cannot_escape_filtered_queryset(self):
        self.confirm(self.alias())
        token = search_profile.set(self.profile)
        try:
            with self.assertRaises(bot.TelegramBotError):
                resolve_name(Producto.objects.exclude(pk=self.product.pk), "aguita")
        finally:
            search_profile.reset(token)

    def test_alias_cannot_override_another_exact_name_or_numeric_id(self):
        other = Producto.objects.create(nombre="OTRO TEST", precio=1, categoria=self.category)
        for alias in (other.nombre, str(other.pk)):
            with self.assertRaises(bot.TelegramBotError):
                self.alias(alias)
        reply = self.alias("nombre nuevo")
        Producto.objects.create(nombre="nombre nuevo", precio=1, categoria=self.category)
        self.confirm(reply)
        self.assertFalse(TelegramAlias.objects.exists())

    def test_selected_result_preserves_original_order_between_questions(self):
        other = Producto.objects.create(nombre="B TEST", precio=1, categoria=self.category)
        original = self.call("consultar_registros", recurso="productos")
        self.assertEqual(len(original.references), 2)
        self.call("consultar_resultado", posicion=1)
        result = self.call("consultar_resultado", posicion=2)
        self.assertEqual(result.references, [original.references[1]])

    def test_result_requires_actual_recent_list_same_chat(self):
        with self.assertRaises(bot.TelegramClarification):
            self.call("consultar_resultado", posicion=1)
        self.call("buscar_producto", consulta=str(self.product.pk))
        for data in ({"posicion": 0}, {"posicion": 2}, {"posicion": 1, "entidad": "ventas"}):
            with self.assertRaises(bot.TelegramClarification):
                self.call("consultar_resultado", **data)
        TelegramAuditoria.objects.update(telegram_chat_id=100)
        with self.assertRaises(bot.TelegramClarification):
            self.call("consultar_resultado", posicion=1)

    def test_old_results_and_relinked_context_are_not_used(self):
        self.call("buscar_producto", consulta=str(self.product.pk))
        TelegramAuditoria.objects.update(creado_en=timezone.now() - timedelta(days=2))
        with self.assertRaises(bot.TelegramClarification):
            self.call("consultar_resultado", posicion=1)

    def test_non_selectable_recent_query_does_not_use_an_older_list(self):
        self.call("consultar_registros", recurso="productos")
        self.call("consultar_turnos")
        with self.assertRaises(bot.TelegramClarification):
            self.call("consultar_resultado", posicion=1)

    def test_read_shortcuts_do_not_drop_a_modification(self):
        from mainApp.services.telegram_shortcuts import specific_read_request
        self.assertEqual(specific_read_request("muéstrame el segundo de la lista"), ("consultar_resultado", {"posicion": 2}))
        self.assertIsNone(specific_read_request("muéstrame el segundo y cambia su precio"))
        self.assertEqual(specific_read_request("mis seguimientos"), ("consultar_seguimientos", {}))

    def test_result_rechecks_permissions_and_record_existence(self):
        self.call("consultar_registros", recurso="productos")
        with patch.object(bot, "user_can_access_url_name", return_value=False), self.assertRaises(PermissionDenied):
            self.call("consultar_resultado", posicion=1)
        self.product.delete()
        with self.assertRaises(bot.TelegramClarification):
            self.call("consultar_resultado", posicion=1)

    def test_plan_resolves_id_then_prepares_exact_catalog_change(self):
        reply = self.workflow(
            self.step("consultar_registros", {"recurso": "productos", "consulta": "AGUA TEST"}),
            self.step("preparar_cambio_catalogo", {"entidad": "producto", "operacion": "editar", "campos": [{"campo": "precio", "valor": "2500"}]}, referencias=[{"campo": "registro_id", "paso": 1}]),
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.precio, Decimal("2000"))
        self.assertTrue(reply.reply_markup)
        self.confirm(reply)
        self.product.refresh_from_db()
        self.assertEqual(self.product.precio, Decimal("2500"))

    def test_plan_rejects_ambiguous_identity_before_proposal(self):
        Producto.objects.create(nombre="AGUA TEST GRANDE", precio=1, categoria=self.category)
        with self.assertRaises(bot.TelegramClarification):
            self.workflow(self.step("consultar_registros", {"recurso": "productos"}),
                self.step("preparar_movimiento_inventario", {"sucursal": str(self.branch.pk), "cantidad": 5, "modo": "sumar", "motivo": "Conteo real"}, referencias=[{"campo": "producto", "paso": 1}]))
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_plan_rejects_recursive_multiple_writes_and_repeated_steps(self):
        write = self.step("preparar_alias", {"entidad": "producto", "alias": "aguita", "registro_id": self.product.pk})
        read = self.step("consultar_capacidades", {})
        for steps in ([write, write], [write, read], [read, read], [self.step("resolver_tarea", {"pasos": []})],
                      [self.step("sql", {"sql": "DELETE"})], [read] * 6):
            with self.subTest(steps=steps), self.assertRaises(bot.TelegramBotError):
                self.workflow(*steps)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_plan_forbids_references_to_amount_or_future_steps(self):
        for field, step in (("cantidad", 1), ("producto", 2)):
            with self.assertRaises(bot.TelegramBotError):
                self.workflow(self.step("consultar_registros", {"recurso": "productos"}),
                    self.step("preparar_movimiento_inventario", {"sucursal": str(self.branch.pk), "modo": "fijar", "motivo": "Conteo real"}, referencias=[{"campo": field, "paso": step}]))

    def test_read_plan_returns_data_without_ai_calls(self):
        with patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No additional IA")):
            reply = self.workflow(self.step("consultar_balance", {}), self.step("consultar_capacidades", {}))
        self.assertIn("saldo real", reply.text)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_restock_lists_registered_offers_without_creating_orders(self):
        self.inventory.cantidad = -3
        self.inventory.save()
        reply = self.call("planificar_reabastecimiento", sucursal=str(self.branch.pk))
        self.assertIn("stock -3", reply.text)
        self.assertIn("$1.200", reply.text)
        self.assertIn("no cotizaciones en vivo", reply.text)
        self.assertFalse(PedidoProveedor.objects.exists())

    def test_balance_analysis_uses_real_periods_and_no_causal_claim(self):
        concept = ConceptoEgreso.objects.create(nombre="TRANSPORTE TEST")
        Egreso.objects.create(concepto=concept, monto=200, medio_pago="efectivo", registrado_por=self.user, registrado_por_nombre="Dueño")
        reply = self.call("analizar_balance")
        self.assertIn("TRANSPORTE TEST: $200", reply.text)
        self.assertIn("Queda $-200", reply.text)
        self.assertIn("no demuestra", reply.text)

    def test_followup_requires_confirmation_and_can_be_paused(self):
        reply = self.call("preparar_seguimiento", tipo="resumen_diario", hora="21:00")
        self.assertFalse(TelegramSeguimiento.objects.exists())
        self.confirm(reply)
        self.assertTrue(TelegramSeguimiento.objects.get().activo)
        reply = self.call("preparar_seguimiento", tipo="resumen_diario", hora="21:00", activo=False)
        self.confirm(reply)
        self.assertFalse(TelegramSeguimiento.objects.get().activo)
        self.assertIn("Sin revisiones", self.call("consultar_seguimientos").text)

    def rule(self, **changes):
        return TelegramSeguimiento.objects.create(telegram_usuario=self.profile, **dict({"tipo": "resumen_diario", "hora": "09:00"}, **changes))

    def run_reports(self, hour=9, day=25):
        now = datetime(2026, 9, day, hour, tzinfo=ZoneInfo("America/Bogota"))
        with patch.object(followups, "is_feature_enabled", return_value=True):
            return followups.process_followups(now=now, client=self.api)

    def test_daily_report_once_per_colombia_day_after_scheduled_time(self):
        self.rule()
        self.assertEqual(self.run_reports(8), 0)
        self.assertEqual(self.run_reports(9), 1)
        self.assertEqual(self.run_reports(21), 0)
        self.api.send_message.assert_called_once()
        self.assertEqual(TelegramEnvioSeguimiento.objects.get().estado, "ENVIADO")
        self.assertEqual(self.run_reports(9, day=26), 1)
        self.assertEqual(self.api.send_message.call_count, 2)

    def test_failed_send_not_repeated_and_does_not_store_token(self):
        self.rule()
        self.api.send_message.side_effect = RuntimeError("token-must-not-appear")
        self.run_reports()
        self.run_reports()
        self.api.send_message.assert_called_once()
        delivery = TelegramEnvioSeguimiento.objects.get()
        self.assertEqual(delivery.estado, "ERROR_INCIERTO")
        self.assertNotIn("token", delivery.detalle)

    def test_no_report_when_paused_inactive_or_feature_disabled(self):
        rule = self.rule(activo=False)
        self.assertEqual(self.run_reports(), 0)
        rule.activo = True
        rule.save()
        with patch.object(followups, "is_feature_enabled", return_value=False):
            self.assertEqual(followups.process_followups(client=self.api), 0)
        self.profile.activo = False
        self.profile.save()
        self.assertEqual(self.run_reports(), 0)
        self.api.send_message.assert_not_called()

    def test_followup_rechecks_permissions_and_private_destination(self):
        self.rule()
        with patch.object(bot, "user_can_access_url_name", return_value=False):
            self.run_reports()
        self.api.send_message.assert_not_called()
        self.assertEqual(TelegramEnvioSeguimiento.objects.get().estado, "OMITIDO")
        self.profile.telegram_chat_id = -800
        self.profile.save()
        self.run_reports(day=26)
        self.api.send_message.assert_not_called()

    def test_stock_report_is_silent_without_low_inventory(self):
        self.rule(tipo="stock_bajo")
        self.run_reports()
        self.api.send_message.assert_not_called()
        self.assertEqual(TelegramEnvioSeguimiento.objects.get().estado, "OMITIDO")

    def test_stock_report_contains_current_counts_without_ai(self):
        self.rule(tipo="stock_bajo")
        self.inventory.cantidad = -3
        self.inventory.save()
        self.run_reports()
        self.assertIn("AGUA TEST", self.api.send_message.call_args.args[1])
        self.assertIn("-3", self.api.send_message.call_args.args[1])

    def test_reserved_delivery_is_never_sent_again(self):
        rule = self.rule()
        TelegramEnvioSeguimiento.objects.create(seguimiento=rule, fecha="2026-09-25", estado="RESERVADO")
        self.assertEqual(self.run_reports(), 0)
        self.api.send_message.assert_not_called()

    def test_followup_hour_changed_while_processing_prevents_early_send(self):
        rule = self.rule()
        original = TelegramSeguimiento.refresh_from_db
        def refresh(instance, *args, **kwargs):
            TelegramSeguimiento.objects.filter(pk=rule.pk).update(hora="23:00")
            return original(instance, *args, **kwargs)
        with patch.object(TelegramSeguimiento, "refresh_from_db", refresh):
            self.run_reports()
        self.api.send_message.assert_not_called()

    def test_commands_are_available_without_intelligent_provider(self):
        update = SimpleNamespace(telegram_user_id=8001, telegram_chat_id=8001)
        for command in ("/alias", "/seguimientos", "/analisis"):
            reply = bot._handle_command(update, self.profile, command)
            self.assertTrue(reply.text)

    def test_router_selects_new_capabilities_and_preserves_bound_history(self):
        for text, name in (("avísame todos los días a las 9", "preparar_seguimiento"),
                           ("prepara un pedido al proveedor", "preparar_pedido_proveedor"),
                           ("fija el stock del producto en 8", "preparar_movimiento_inventario"),
                           ("recuerda el alias aguita", "preparar_alias"),
                           ("busca el producto y luego cambia su precio", "resolver_tarea"),
                           ("dame el segundo de la lista", "consultar_resultado")):
            self.assertIn(name, selected_tool_names(text, [], bot.TOOL_FUNCTIONS))
        history = [{"role": "user", "text": str(i)} for i in range(30)]
        self.assertEqual(len(compact_history(history)), 24)

    def test_shared_web_order_creation_is_atomic_and_validates_details(self):
        from mainApp.views import PedidoProveedorCreateAJAXView
        data = {"proveedor": self.provider.pk, "sucursal": self.branch.pk,
                "detalles": json.dumps([{"productoid": self.product.pk, "cantidad": 2, "precio_unitario": "1200"}])}
        request = RequestFactory().post("/agregar_pedido/", data)
        response = PedidoProveedorCreateAJAXView().post(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PedidoProveedor.objects.get().costototal, Decimal("2400"))
        for pid in (1.5, True, "abc"):
            invalid = dict(data, detalles=json.dumps([{"productoid": pid, "cantidad": 1, "precio_unitario": "1200"}]))
            with self.assertRaises(BusinessOperationError):
                create_supplier_order(invalid)
        self.assertEqual(PedidoProveedor.objects.count(), 1)

    def test_shared_inventory_web_keeps_response_contract(self):
        from mainApp.views import EditarInventarioView
        request = RequestFactory().post("/editar_inventario/", {"action": "add_item", "productoid": self.product.pk, "add_cantidad": "-3"})
        with patch("mainApp.views.messages.success"):
            response = EditarInventarioView().post(request, self.branch.pk)
        data = json.loads(response.content)
        self.assertEqual(data["new_cantidad"], 7)
        self.assertEqual(data["mode"], "add")

    @override_settings(MIGRATION_MODULES={})
    def test_migration_state_matches_new_models_without_applying_to_database(self):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        state = loader.project_state([("mainApp", "0042_telegram_assistant_workspace")])
        for model in (TelegramAlias, TelegramSeguimiento, TelegramEnvioSeguimiento):
            self.assertEqual(state.models[("mainApp", model._meta.model_name)], ModelState.from_model(model))
