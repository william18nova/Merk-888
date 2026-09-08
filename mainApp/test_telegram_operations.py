from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from mainApp.models import (
    Categoria, Cliente, DetallePedidoProveedor, DetalleVenta, Empleado,
    Inventario, NotificacionNequi, PagoVenta, PedidoProveedor, Producto,
    Proveedor, PuntosPago, Rol, Sucursal, TelegramAccionPendiente, TelegramActualizacion,
    TelegramAuditoria, TelegramUsuario, TurnoCaja, TurnoCajaMedio, Usuario, Venta,
)
from mainApp.services import telegram_bot as bot
from mainApp.services import telegram_operations as ops


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True)
class TelegramOperationsTests(TestCase):
    def setUp(self):
        self.role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("Operador prueba", rolid=self.role)
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=771, telegram_chat_id=771)
        self.callback_client = SimpleNamespace(answer_callback=MagicMock())
        self.branch = Sucursal.objects.create(nombre="Yerbabuena", telefono="3001234567")
        self.category = Categoria.objects.create(nombre="BEBIDAS")
        self.product = Producto.objects.create(nombre="AGUA NATURAL", precio=Decimal("1500.50"), categoria=self.category, codigo_de_barras="000012345")
        self.provider = Proveedor.objects.create(nombre="Proveedor prueba", empresa="Distribuidor", telefono="3007654321", email="privado@example.test")
        self.point = PuntosPago.objects.create(nombre="Caja principal", sucursalid=self.branch)
        employee = Empleado(nombre="Ana", apellido="Pérez", telefono="3009999999", email="ana@example.test", puesto="Cajera", numerodocumento="999999", sucursalid=self.branch)
        Empleado.objects.bulk_create([employee])
        self.employee = employee

    def query(self, name="consultar_registros", **args):
        return bot._execute_tool(self.profile, name, args)

    def prepare(self, entity="categoria", operation="crear", record=None, **fields):
        args = {"entidad": entity, "operacion": operation, "campos": [{"campo": key, "valor": str(value)} for key, value in fields.items()]}
        if record is not None:
            args["registro_id"] = record
        return self.query("preparar_cambio_catalogo", **args)

    def callback(self, reply, label="Confirmar", profile=None):
        button = next(button for row in reply.reply_markup["inline_keyboard"] for button in row if label in button["text"])
        update = SimpleNamespace(texto=button["callback_data"], callback_query_id="test-operations")
        return bot._handle_callback(update, profile or self.profile, self.callback_client)

    def sale(self, product=None, quantity=2, day=None):
        sale = Venta.objects.create(fecha=day or timezone.localdate(), hora="12:00", empleadoid=self.employee, sucursalid=self.branch, puntopagoid=self.point, total="3001", mediopago="nequi")
        DetalleVenta.objects.create(ventaid=sale, productoid=product or self.product, cantidad=quantity, preciounitario="1500.50")
        return sale

    def message(self, text, voice=False):
        return TelegramActualizacion.objects.create(
            update_id=20000 + TelegramActualizacion.objects.count(),
            telegram_user_id=self.profile.telegram_user_id,
            telegram_chat_id=self.profile.telegram_chat_id,
            tipo="VOZ" if voice else "TEXTO",
            texto="" if voice else text,
            transcripcion=text if voice else "",
        )

    def test_clear_sale_id_queries_work_without_ai_for_text_and_voice(self):
        sale = self.sale(day=timezone.localdate() - timedelta(days=90))
        PagoVenta.objects.create(ventaid=sale, medio_pago="nequi", monto=3001)
        requests = (
            "Muéstrame la venta de id {id}", "muestrame la venta {id}",
            "¿Me puedes mostrar la factura número {id}?",
            "Quiero ver la venta con el ID: {id}",
            "Dame los datos de la venta #{id}", "Detalle de la venta {id}",
            "Muéstrame los detalles de la venta {id}.",
            "Muéstrame los detalles de la venta {id}.",
            "Por favor, muestra los productos de la venta {id}",
            "VENTA {id}", "factura #{id}", "¿Puedes mostrarme la venta {id}?",
            "Necesito consultar la venta {id}",
            "Muestra la información de la venta {id}, por favor.",
            "Hola, me muestras la venta {id}",
        )
        with patch.object(bot, "_intelligent_function_call", side_effect=bot.TelegramConfigurationError("IA no disponible")) as ai:
            for voice in (False, True):
                for text in requests:
                    with self.subTest(text=text, voice=voice):
                        reply = bot.build_reply(self.message(text.format(id=sale.pk), voice), self.callback_client)
                        self.assertEqual(reply.intent, "consultar_detalle_operativo")
                        self.assertIn(f"Venta #{sale.pk}", reply.text)
                        self.assertIn(self.product.nombre, reply.text)
                        self.assertIn("$3.001", reply.text)
                        self.assertIn("Nequi", reply.text)
                        self.assertIn("Ana Pérez", reply.text)
                        self.assertIn(self.point.nombre, reply.text)
                        self.assertIn("Hora: 12:00:00", reply.text)
            ai.assert_not_called()
        sale.refresh_from_db()
        self.assertEqual(sale.total, Decimal("3001"))
        self.assertFalse(TelegramAccionPendiente.objects.exists())
        self.assertTrue(TelegramAuditoria.objects.filter(accion="consultar_detalle_operativo", exitoso=True).exists())

    def test_sale_and_invoice_commands_support_id_and_bot_mention(self):
        sale = self.sale()
        with patch.object(bot, "_intelligent_function_call") as ai:
            for command in (f"/venta {sale.pk}", f"/factura #{sale.pk}", f"/venta@Merk2888Bot\n{sale.pk}"):
                with self.subTest(command=command):
                    reply = bot.build_reply(self.message(command), self.callback_client)
                    self.assertIn(f"Venta #{sale.pk}", reply.text)
            ai.assert_not_called()

    def test_sale_command_rejects_missing_multiple_or_invalid_ids(self):
        with patch.object(bot, "_execute_tool") as tool:
            for value in ("", "xxxxx", "0", "-12", "12.5", "12 13", "1 OR 1=1", "1" * 40):
                with self.subTest(value=value):
                    reply = bot.build_reply(self.message(f"/venta {value}"), self.callback_client)
                    self.assertEqual(reply.intent, "ayuda_venta")
            tool.assert_not_called()

    def test_direct_sale_parser_does_not_override_other_actions_or_filters(self):
        requests = (
            "Devuelve de la venta 142266 el producto 12 cantidad 1 en efectivo",
            "Anula la venta 142266", "Cambia la venta 142266", "Paga la factura 142266",
            "Muestra la venta 142266 y devuelve un producto", "Muestra la venta 142266 y 142267",
            "Muestra la venta 142266 de ayer", "Muestra las ventas de hoy",
            "Cuánto vendí hoy", "Detalle del pedido 142266", "Venta -142266",
            "venta 142266.5", "venta 142266 OR 1=1", "142266", "venta xxxxx",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertIsNone(bot._sale_detail_request(text))
        update = self.message(requests[0])
        args = {"venta_id": 142266, "productos": [{"producto_id": 12, "cantidad": 1}]}
        with patch.object(bot, "_intelligent_function_call", return_value=("preparar_devolucion_venta", args, "")), patch.object(bot, "_execute_tool", return_value=bot.BotReply("Preparar")) as execute:
            bot.build_reply(update, self.callback_client)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[1:3], ("preparar_devolucion_venta", args))

    def test_sale_queries_require_link_and_route_permission_not_edit_permission(self):
        sale = self.sale()
        update = self.message(f"Muéstrame la venta {sale.pk}")
        with patch.object(bot, "user_can_access_url_name", return_value=False), patch.object(bot, "_intelligent_function_call") as ai:
            with self.assertRaises(PermissionDenied):
                bot.build_reply(update, self.callback_client)
            ai.assert_not_called()
        with patch.object(bot, "user_can_access_url_name", side_effect=lambda user, name: name == "ver_venta"):
            reply = bot.build_reply(update, self.callback_client)
            self.assertIn(f"Venta #{sale.pk}", reply.text)
        self.profile.activo = False
        self.profile.save(update_fields=["activo"])
        with patch.object(bot, "_execute_tool") as tool:
            self.assertEqual(bot.build_reply(update, self.callback_client).intent, "sin_vinculo")
            tool.assert_not_called()

    def test_nonexistent_sale_does_not_fall_back_to_ai_or_create_records(self):
        with patch.object(bot, "_intelligent_function_call") as ai:
            with self.assertRaisesMessage(bot.TelegramBotError, "No encontré esa venta"):
                bot.build_reply(self.message("Muéstrame la venta 999999"), self.callback_client)
            ai.assert_not_called()
        self.assertFalse(Venta.objects.exists())
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_sale_details_page_buttons_retain_sale_id(self):
        sale = self.sale()
        for _ in range(6):
            DetalleVenta.objects.create(ventaid=sale, productoid=self.product, cantidad=1, preciounitario="1500.50")
        first = bot.build_reply(self.message(f"/venta {sale.pk}"), self.callback_client)
        second = self.callback(first, "Siguiente")
        self.assertIn("página 2 de 2", second.text)
        self.assertIn(f"Venta #{sale.pk}", second.text)
        self.assertEqual(second.pagination["arguments"]["id"], sale.pk)

    def test_cashier_sale_details_and_lists_stay_in_assigned_branch(self):
        sale = self.sale()
        other_sale = self.sale()
        other_branch = Sucursal.objects.create(nombre="Privada")
        other_sale.sucursalid = other_branch
        other_sale.save(update_fields=["sucursalid"])
        self.user.rolid = Rol.objects.create(nombre="Cajero")
        self.user.save(update_fields=["rolid"])
        Empleado.objects.filter(pk=self.employee.pk).update(usuarioid=self.user)
        with patch.object(bot, "user_can_access_url_name", return_value=True):
            reply = bot.build_reply(self.message(f"/venta {sale.pk}"), self.callback_client)
            self.assertIn(f"Venta #{sale.pk}", reply.text)
            with self.assertRaisesMessage(bot.TelegramBotError, "No encontré esa venta"):
                bot.build_reply(self.message(f"/venta {other_sale.pk}"), self.callback_client)
            self.assertNotIn("Privada", self.query(recurso="ventas").text)
            Empleado.objects.filter(pk=self.employee.pk).update(usuarioid=None)
            with self.assertRaises(bot.TelegramBotError):
                bot.build_reply(self.message(f"/venta {sale.pk}"), self.callback_client)

    def test_plural_sales_command_still_returns_today_summary(self):
        update = self.message("/ventas")
        with patch.object(bot, "_execute_tool", return_value=bot.BotReply("Ventas hoy")) as execute:
            bot.build_reply(update, self.callback_client)
        self.assertEqual(execute.call_args.args[1:3], ("consultar_ventas", {}))

    def test_every_registered_resource_uses_existing_model_fields_and_permissions(self):
        for name, spec in ops.RESOURCES.items():
            with self.subTest(resource=name):
                self.assertTrue(reverse(spec.permission, kwargs={"venta_id": 1} if spec.permission == "ver_venta" else None))
                reply = self.query(recurso=name)
                self.assertRegex(reply.text, r"\d+ resultados?")
                self.assertLess(len(reply.text), 4000)

    def test_all_reads_deny_user_without_the_corresponding_permission(self):
        with patch.object(bot, "user_can_access_url_name", return_value=False):
            for name in ops.RESOURCES:
                with self.subTest(resource=name), self.assertRaises(PermissionDenied):
                    self.query(recurso=name)
            for name, args in (("ranking_productos", {}), ("buscar_vistas", {}), ("consultar_capacidades", {}), ("consultar_detalle_operativo", {"tipo": "venta", "id": 1})):
                with self.subTest(tool=name), self.assertRaises(PermissionDenied):
                    self.query(name, **args)

    def test_inactive_user_cannot_read_or_propose(self):
        self.user.is_active = False
        with self.assertRaises(PermissionDenied):
            self.query(recurso="categorias")
        with self.assertRaises(PermissionDenied):
            self.prepare(nombre="NO CREAR")

    def test_resources_never_expose_passwords_or_nequi_raw_payload(self):
        self.user.password = "PASSWORD NEVER SHOW"
        self.user.save(update_fields=["password"])
        NotificacionNequi.objects.create(texto="PRIVATE MESSAGE", remitente="Remitente", fingerprint="PRIVATE FINGERPRINT", raw_payload={"token": "SECRET API KEY"}, es_ingreso=True, monto=10)
        text = self.query(recurso="usuarios").text + self.query(recurso="nequi").text
        for secret in ("PASSWORD NEVER SHOW", "PRIVATE MESSAGE", "PRIVATE FINGERPRINT", "SECRET API KEY"):
            self.assertNotIn(secret, text)

    def test_contact_details_only_when_explicitly_requested(self):
        normal = self.query(recurso="proveedores")
        detailed = self.query(recurso="proveedores", incluir_contacto=True)
        self.assertNotIn(self.provider.email, normal.text)
        self.assertIn(self.provider.email, detailed.text)

    def test_no_arbitrary_models_or_unsupported_filters(self):
        for args in ({"recurso": "TelegramCodigoVinculacion"}, {"recurso": "categorias", "sucursal": "Yerbabuena"}, {"recurso": "clientes", "estado": "activo"}, {"recurso": "clientes", "desde": "2026-01-01"}, {"recurso": "clientes", "sin_ventas": True}, {"recurso": "productos", "stock_max": 2}, {"recurso": "nequi", "vinculado": "false"}):
            with self.subTest(args=args), self.assertRaises(bot.TelegramBotError):
                self.query(**args)

    def test_safe_numeric_ids_search_and_count_without_detail(self):
        found = self.query(recurso="productos", consulta="000012345")
        self.assertIn(f"#{self.product.pk}", found.text)
        count = self.query(recurso="productos", solo_total=True)
        self.assertIn("1 registro", count.text)
        self.assertNotIn("AGUA NATURAL", count.text)
        with self.assertRaises(bot.TelegramBotError):
            self.query(recurso="productos", registro_id="1 OR 1=1")

    def test_unsold_products_use_all_recorded_sales_not_only_today(self):
        self.sale(day=timezone.localdate() - timedelta(days=800))
        never = Producto.objects.create(nombre="FLOTADOR SIN VENTAS", precio=3000, categoria=self.category)
        reply = self.query(recurso="productos", sin_ventas=True)
        self.assertIn(never.nombre, reply.text)
        self.assertNotIn(self.product.nombre, reply.text)

    def test_stock_threshold_filters_branch_and_zero_or_negative_quantity(self):
        other = Sucursal.objects.create(nombre="Otra")
        Inventario.objects.create(productoid=self.product, sucursalid=self.branch, cantidad=0)
        Inventario.objects.create(productoid=self.product, sucursalid=other, cantidad=9)
        reply = self.query(recurso="inventario", stock_max=0, sucursal="Yerbabuena")
        self.assertIn("Cantidad: 0", reply.text)
        self.assertNotIn("Otra", reply.text)
        for value in ("nan", "abc", 1.5, 99999999999999):
            with self.subTest(value=value), self.assertRaises(bot.TelegramBotError):
                self.query(recurso="inventario", stock_max=value)

    def test_nequi_only_incoming_and_link_filter(self):
        sale = self.sale()
        for index, incoming, linked in ((1, True, False), (2, True, True), (3, False, False)):
            NotificacionNequi.objects.create(texto="test", remitente=f"Persona {index}", fingerprint=str(index), es_ingreso=incoming, venta=sale if linked else None, monto=index)
        unlinked = self.query(recurso="nequi", vinculado=False)
        linked = self.query(recurso="nequi", vinculado=True)
        self.assertIn("Persona 1", unlinked.text)
        self.assertNotIn("Persona 2", unlinked.text)
        self.assertNotIn("Persona 3", unlinked.text)
        self.assertIn("Persona 2", linked.text)

    def test_event_dates_are_anchored_and_exact_id_ignores_today(self):
        sale = self.sale(day=timezone.localdate() - timedelta(days=20))
        today = self.query(recurso="ventas")
        self.assertIn("0 resultados", today.text)
        exact = self.query(recurso="ventas", registro_id=str(sale.pk))
        self.assertIn(f"#{sale.pk}", exact.text)
        audit = TelegramAuditoria.objects.filter(accion="consultar_registros").order_by("pk").first()
        self.assertEqual(audit.argumentos["desde"], timezone.localdate().isoformat())

    def test_pages_preserve_scope_and_recheck_permissions(self):
        Categoria.objects.bulk_create([Categoria(nombre=f"CAT {i}") for i in range(7)])
        first = self.query(recurso="categorias", consulta="CAT")
        second = self.callback(first, "Siguiente")
        self.assertIn("página 2 de 2", second.text)
        self.assertIn("búsqueda: CAT", second.text)
        with patch.object(bot, "user_can_access_url_name", return_value=False), self.assertRaises(PermissionDenied):
            self.callback(first, "Siguiente")

    def test_views_only_link_real_permitted_pages_without_actions(self):
        with patch.object(bot, "user_can_access_url_name", side_effect=lambda user, name: name in {"home", "registrar_egreso"}):
            reply = self.query("buscar_vistas", consulta="pagos")
        self.assertIn(reverse("registrar_egreso"), reply.text)
        self.assertNotIn(reverse("configuracion_telegram_bot"), reply.text)
        self.assertIn("no ejecutan", reply.text)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_capabilities_show_only_allowed_catalogs_and_modifications(self):
        with patch.object(bot, "user_can_access_url_name", side_effect=lambda user, name: name in {"home", "registrar_egreso"}):
            reply = self.query("consultar_capacidades")
        self.assertIn("conceptos pago", reply.text)
        self.assertNotIn("producto (crear", reply.text)
        self.assertNotIn("usuarios, roles", reply.text)
        for entity in ops.EDITABLE:
            self.assertIn("Obligatorios", self.query("consultar_capacidades", entidad=entity).text)

    def test_new_tools_are_registered_for_both_providers(self):
        gemini = {tool["name"] for tool in bot.GEMINI_TOOLS[0]["functionDeclarations"]}
        groq = {tool["function"]["name"] for tool in bot.GROQ_CHAT_TOOLS}
        self.assertEqual(gemini, groq)
        self.assertTrue(set(ops.TOOL_FUNCTIONS) <= gemini)
        self.assertEqual(len(gemini), len(bot.TOOL_FUNCTIONS))

    def test_commands_bypass_ai_and_voice_can_call_same_tools(self):
        update = SimpleNamespace(texto="/catalogo categorias")
        with patch.object(bot, "_intelligent_function_call") as ai:
            self.assertIn("BEBIDAS", bot._handle_command(update, self.profile, update.texto).text)
            self.assertIn("Campos de producto", bot._handle_command(update, self.profile, "/acciones producto").text)
            self.assertIn("Páginas", bot._handle_command(update, self.profile, "/vistas caja").text)
            ai.assert_not_called()
        from mainApp.models import TelegramActualizacion
        voice = TelegramActualizacion.objects.create(update_id=9922, telegram_user_id=771, telegram_chat_id=771, tipo="VOZ", transcripcion="Muestra las categorías")
        with patch.object(bot, "_intelligent_function_call", return_value=("consultar_registros", {"recurso": "categorias"}, "")) as ai:
            reply = bot.build_reply(voice, self.callback_client)
        self.assertEqual(ai.call_args.args[0], voice.transcripcion)
        self.assertIn("BEBIDAS", reply.text)

    def test_sale_order_shift_details_are_paginated_and_read_only(self):
        sale = self.sale()
        PagoVenta.objects.create(ventaid=sale, medio_pago="nequi", monto=3001)
        reply = self.query("consultar_detalle_operativo", tipo="venta", id=sale.pk)
        self.assertIn("$3.001", reply.text)
        self.assertIn(self.product.nombre, reply.text)
        self.assertIn("Nequi", reply.text)
        order = PedidoProveedor.objects.create(proveedorid=self.provider, sucursalid=self.branch, costototal=8000, monto_pagado=6000)
        DetallePedidoProveedor.objects.create(pedidoid=order, productoid=self.product, cantidad=4, preciounitario=2000)
        reply = self.query("consultar_detalle_operativo", tipo="pedido", id=order.pk)
        self.assertIn("$8.000", reply.text)
        self.assertIn("$6.000", reply.text)
        shift = TurnoCaja.objects.create(puntopago=self.point, cajero=self.user, estado="CERRADO", ventas_total=10000)
        TurnoCajaMedio.objects.create(turno=shift, metodo="facturas_pagadas", contado=1500)
        reply = self.query("consultar_detalle_operativo", tipo="turno", id=shift.pk)
        self.assertIn("$1.500", reply.text)
        self.assertIn("no se suma otra vez", reply.text)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_ranking_uses_decimal_arithmetic_and_registered_quantity(self):
        self.sale(quantity=3)
        low = Producto.objects.create(nombre="MENOS VENDIDO", categoria=self.category, precio=10)
        self.sale(product=low, quantity=1)
        self.sale(product=low, quantity=99, day=timezone.localdate() - timedelta(days=9))
        reply = self.query("ranking_productos", orden="cantidad")
        self.assertIn("cantidad 3; importe $4.501,50", reply.text)
        self.assertLess(reply.text.index(self.product.nombre), reply.text.index(low.nombre))
        ascending = self.query("ranking_productos", orden="cantidad", ascendente=True)
        self.assertLess(ascending.text.index(low.nombre), ascending.text.index(self.product.nombre))

    def test_prepare_does_not_save_and_confirm_is_idempotent_and_audited(self):
        reply = self.prepare(nombre="FRUTAS", descripcion="Productos frescos")
        self.assertFalse(Categoria.objects.filter(nombre="FRUTAS").exists())
        self.assertIn("Todavía no he cambiado nada", reply.text)
        self.assertIn("Listo, guardé", self.callback(reply).text)
        self.assertEqual(Categoria.objects.filter(nombre="FRUTAS").count(), 1)
        self.assertIn("ya estaba", self.callback(reply).text)
        self.assertEqual(Categoria.objects.filter(nombre="FRUTAS").count(), 1)
        audit = TelegramAuditoria.objects.get(accion="confirmar_cambio_catalogo")
        self.assertEqual(audit.usuario, self.user)
        self.assertTrue(audit.exitoso)

    def test_create_each_allowed_entity_reuses_forms(self):
        cases = (
            ("sucursal", {"nombre": "LOCAL NUEVO", "telefono": "3001234568", "direccion": "Centro"}),
            ("proveedor", {"nombre": "COCA-COLA", "empresa": "EMBOTELLADORA", "telefono": "3002222222", "email": "proveedor@example.com"}),
            ("cliente", {"nombre": "Pedro", "apellido": "Ramirez", "numerodocumento": "001122", "telefono": "3003333333", "email": "pedro@example.com"}),
            ("producto", {"nombre": "GASEOSA", "precio": "2800.50", "categoria": str(self.category.pk), "iva": "0.19", "codigo_de_barras": "00009999"}),
        )
        for entity, fields in cases:
            with self.subTest(entity=entity):
                reply = self.prepare(entity=entity, **fields)
                confirmed = self.callback(reply)
                self.assertIn("Listo, guardé el cambio", confirmed.text)
                self.assertTrue(ops._model(ops.EDITABLE[entity][0]).objects.filter(nombre=fields["nombre"]).exists())
        self.assertEqual(Cliente.objects.get(nombre="Pedro").numerodocumento, "001122")
        self.assertEqual(Producto.objects.get(nombre="GASEOSA").codigo_de_barras, "00009999")

    def test_edit_price_preserves_old_price_and_other_product_fields(self):
        reply = self.prepare(entity="producto", operation="editar", record=self.product.pk, precio="1900.75")
        self.product.refresh_from_db()
        self.assertEqual(self.product.precio, Decimal("1500.50"))
        self.assertIn("1500.50 → 1900.75", reply.text)
        self.callback(reply)
        self.product.refresh_from_db()
        self.assertEqual(self.product.precio, Decimal("1900.75"))
        self.assertEqual(self.product.precio_anterior, Decimal("1500.50"))
        self.assertEqual(self.product.codigo_de_barras, "000012345")
        reply = self.prepare(entity="producto", operation="editar", record=self.product.pk, nombre="AGUA SIN GAS")
        self.callback(reply)
        self.product.refresh_from_db()
        self.assertEqual(self.product.precio_anterior, Decimal("1500.50"))

    def test_concurrent_change_rejects_stale_proposal_without_overwriting(self):
        reply = self.prepare(entity="producto", operation="editar", record=self.product.pk, precio="5000")
        Producto.objects.filter(pk=self.product.pk).update(nombre="EDITADO EN WEB")
        confirmed = self.callback(reply)
        self.product.refresh_from_db()
        self.assertIn("cambió desde la propuesta", confirmed.text)
        self.assertEqual(self.product.precio, Decimal("1500.50"))
        self.assertEqual(self.product.nombre, "EDITADO EN WEB")
        self.assertEqual(TelegramAccionPendiente.objects.get().estado, "ERROR")

    def test_invalid_financial_values_and_privileged_mutations_are_rejected(self):
        for fields in ({"precio": "-1"}, {"iva": "19"}, {"rentabilidad": "101"}, {"icui": "-1"}, {"precio": "nan"}, {"categoria": "999999"}, {"password": "NO"}, {"precio_anterior": "0"}):
            with self.subTest(fields=fields), self.assertRaises(bot.TelegramBotError):
                self.prepare(entity="producto", operation="editar", record=self.product.pk, **fields)
        with self.assertRaises(bot.TelegramBotError):
            self.prepare(entity="usuario", nombre="NO")
        with self.assertRaises(bot.TelegramBotError):
            self.prepare(operation="eliminar", record=self.category.pk, nombre="NO")
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_protected_category_and_missing_required_data_are_rejected(self):
        category = Categoria.objects.create(nombre="Sin categoría")
        with self.assertRaises(bot.TelegramBotError):
            self.prepare(operation="editar", record=category.pk, nombre="NUEVA")
        with self.assertRaises(bot.TelegramBotError):
            self.prepare(entity="cliente", nombre="Pedro")
        with self.assertRaises(bot.TelegramBotError):
            self.prepare(entity="producto", nombre="INCOMPLETO")

    def test_permission_revocation_cancel_expiration_and_foreign_buttons(self):
        reply = self.prepare(nombre="NO GUARDAR")
        with patch.object(bot, "user_can_access_url_name", return_value=False), self.assertRaises(PermissionDenied):
            self.callback(reply)
        other = Usuario.objects.create_user("Otro", rolid=self.role)
        profile = TelegramUsuario.objects.create(usuario=other, telegram_user_id=772, telegram_chat_id=772)
        self.assertIn("otra cuenta", self.callback(reply, profile=profile).text)
        self.assertIn("descarté esta solicitud", self.callback(reply, "Cancelar").text)
        self.assertIn("ya estaba", self.callback(reply).text)
        reply = self.prepare(nombre="NO GUARDAR")
        TelegramAccionPendiente.objects.filter(estado="PENDIENTE").update(vence_en=timezone.now() - timedelta(seconds=1))
        self.assertIn("pasó el tiempo para confirmar", self.callback(reply).text)
        self.assertFalse(Categoria.objects.filter(nombre="NO GUARDAR").exists())

    def test_integrity_failure_rolls_back_savepoint_and_marks_action(self):
        reply = self.prepare(nombre="CONFLICTO")
        with patch.object(Categoria, "save", side_effect=IntegrityError("private SQL detail")):
            result = self.callback(reply)
        self.assertIn("No se guardó", result.text)
        self.assertNotIn("private SQL", result.text)
        self.assertEqual(TelegramAccionPendiente.objects.get().estado, "ERROR")
        self.assertFalse(Categoria.objects.filter(nombre="CONFLICTO").exists())

    def test_forms_revalidate_uniqueness_at_confirmation(self):
        reply = self.prepare(nombre="FRUTAS")
        Categoria.objects.create(nombre="FRUTAS")
        self.assertIn("No se guardó", self.callback(reply).text)
        self.assertEqual(Categoria.objects.filter(nombre="FRUTAS").count(), 1)

    def test_noop_does_not_create_pending_action(self):
        reply = self.prepare(operation="editar", record=self.category.pk, nombre=self.category.nombre)
        self.assertIn("Ya está guardado así", reply.text)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_edit_by_exact_unique_name_and_ambiguous_name_is_not_chosen(self):
        reply = self.query("preparar_cambio_catalogo", entidad="producto", operacion="editar", registro="agua natural", campos=[{"campo": "precio", "valor": "2000"}])
        self.assertIn(f"#{self.product.pk} (AGUA NATURAL)", reply.text)
        self.callback(reply)
        self.product.refresh_from_db()
        self.assertEqual(self.product.precio, Decimal("2000"))
        Categoria.objects.create(nombre="DUPLICADA")
        Categoria.objects.create(nombre="DUPLICADA")
        with self.assertRaises(bot.TelegramBotError):
            self.query("preparar_cambio_catalogo", entidad="categoria", operacion="editar", registro="duplicada", campos=[{"campo": "nombre", "valor": "NUEVA"}])
        self.assertFalse(Categoria.objects.filter(nombre="NUEVA").exists())

    def test_category_filter_and_strict_tool_schema(self):
        other = Categoria.objects.create(nombre="ASEO")
        Producto.objects.create(nombre="JABON", categoria=other, precio=2000)
        reply = self.query(recurso="productos", categoria="bebidas")
        self.assertIn(self.product.nombre, reply.text)
        self.assertNotIn("JABON", reply.text)
        for arguments in ({"recurso": "productos", "categoria": "INEXISTENTE"}, {"recurso": "productos", "sql": "select 1"}, {"recurso": "productos", "sin_ventas": "false"}, {"recurso": "productos", "solo_total": 1}):
            with self.subTest(arguments=arguments), self.assertRaises(bot.TelegramBotError):
                self.query(**arguments)

    def test_long_confirmation_is_not_truncated_or_saved(self):
        with self.assertRaises(bot.TelegramBotError):
            self.prepare(nombre="NUEVA", descripcion="x" * 1001)
        self.assertFalse(TelegramAccionPendiente.objects.exists())
