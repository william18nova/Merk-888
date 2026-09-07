import json
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from mainApp.models import (
    Categoria, ConceptoEgreso, DetalleVenta, Egreso, Empleado, Inventario, MetodoPago,
    Producto, PuntosPago, Rol, Sucursal, TelegramAccionPendiente, TelegramActualizacion,
    TelegramAuditoria, TelegramUsuario, Usuario, Venta,
)
from mainApp.services import telegram_bot as bot
from mainApp.services import telegram_queries as queries
from mainApp.services import telegram_search as search
from mainApp.services.telegram_returns import _select_lines


class SimilarityTests(SimpleTestCase):
    def test_accents_punctuation_word_order_and_small_typos(self):
        for query, label in (("cocacola", "COCA-COLA"), ("limon", "LIMÓN"), ("Nova William", "William Nova"), ("aroz diana", "ARROZ DIANA 500 G"), ("yerbabuena", "Yerbabuena")):
            with self.subTest(query=query):
                self.assertGreaterEqual(search.similarity(query, label), 0.8)

    def test_sizes_codes_and_short_names_are_not_guessed(self):
        for query, label in (("ARROZ 500 G", "ARROZ 1000 G"), ("0001234", "0001235"), ("AB", "ABC"), ("arroz 12", "arroz 21")):
            with self.subTest(query=query):
                self.assertEqual(search.similarity(query, label), 0)

    def test_tied_names_require_a_choice(self):
        candidates = search.rank_candidates("aroz diana", [(1, "ARROZ DIANA 500 G", ()), (2, "ARROZ DIANA 1000 G", ())])
        with self.assertRaisesMessage(bot.TelegramBotError, "¿Cuál quieres usar?") as raised:
            search.choose_match("aroz diana", candidates)
        self.assertIn("ID 1", str(raised.exception))
        self.assertIn("ID 2", str(raised.exception))

    def test_exact_match_outranks_similarity(self):
        candidates = search.rank_candidates("COCA-COLA", [(1, "COCA-COLA", ()), (2, "COCA-COLA ZERO", ())])
        self.assertEqual(search.choose_match("COCA-COLA", candidates).pk, 1)

    def test_catalog_scan_is_bounded_and_never_silently_truncated(self):
        with patch.object(search, "MAX_CANDIDATES", 2):
            with self.assertRaisesMessage(bot.TelegramBotError, "demasiados"):
                search.rank_candidates("arroz", [(i, "ARROZ", ()) for i in range(3)])

    def test_complex_queries_are_available_in_full_and_compact_ai_context(self):
        for text in ("Cuánto arroz se vendió por sucursal", "Pagos mayores de 5000 por concepto", "Necesito un análisis diferente"):
            with self.subTest(text=text):
                definitions, prompt, _ = bot._ai_request_context(text)
                self.assertIn("consultar_datos", {item["name"] for item in definitions})
                self.assertIn("nunca envíes SQL", prompt)
                self.assertIn("No inventes IDs", prompt)


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True)
class SmartQueriesTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user("Prueba consultas", rolid=Rol.objects.create(nombre="Web Master"))
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=9931, telegram_chat_id=9931)
        self.client_stub = SimpleNamespace(answer_callback=MagicMock())
        self.branch = Sucursal.objects.create(nombre="Yerbabuena", telefono="3001234567")
        self.category = Categoria.objects.create(nombre="GRANOS")
        self.product = Producto.objects.create(nombre="ARROZ DIANA 500 G", precio="1000.25", categoria=self.category, codigo_de_barras="000012345")
        self.other = Producto.objects.create(nombre="LENTEJA VERDE", precio="2000.50", categoria=self.category, codigo_de_barras="000099999")
        self.point = PuntosPago.objects.create(nombre="Caja principal", sucursalid=self.branch)
        employee = Empleado(nombre="William", apellido="Nova", telefono="3012345678", email="test@example.test", puesto="Cajero", numerodocumento="TESTSMART1", sucursalid=self.branch)
        Empleado.objects.bulk_create([employee])
        self.employee = employee
        self.sale = Venta.objects.create(fecha=timezone.localdate(), hora="12:00", empleadoid=employee, sucursalid=self.branch, puntopagoid=self.point, total="3000.75", mediopago="nequi")
        self.detail = DetalleVenta.objects.create(ventaid=self.sale, productoid=self.product, cantidad=3, preciounitario="1000.25")
        Inventario.objects.create(productoid=self.product, sucursalid=self.branch, cantidad=5)
        self.concept = ConceptoEgreso.objects.create(nombre="COCA-COLA")
        self.expense = Egreso.objects.create(concepto=self.concept, monto="1000.25", medio_pago="efectivo", registrado_por_nombre="William Nova")
        MetodoPago.objects.update_or_create(codigo="efectivo", defaults={"nombre": "Efectivo", "activo": True, "es_efectivo": True})

    def query(self, source="pagos", **args):
        return bot._execute_tool(self.profile, "consultar_datos", {"fuente": source, **args})

    def test_product_search_orders_similar_names_and_keeps_ids(self):
        text = bot.tool_find_product(self.profile, {"consulta": "aroz diana"})
        self.assertIn(f"ID {self.product.pk}", text)
        self.assertIn(self.product.nombre, text)
        self.assertNotIn(self.other.nombre, text)

    def test_numeric_product_code_is_exact(self):
        self.assertIn(self.product.nombre, bot.tool_find_product(self.profile, {"consulta": "000012345"}))
        self.assertNotIn(self.product.nombre, bot.tool_find_product(self.profile, {"consulta": "000012346"}))

    def test_inventory_and_employees_search_by_similar_name(self):
        self.assertIn(self.product.nombre, bot.tool_inventory(self.profile, {"consulta": "aroz diana", "sucursal": "yerbabuena"}))
        self.assertIn("William Nova", bot.tool_employees(self.profile, {"consulta": "wlliam nova"}).text)
        self.assertEqual(bot._find_branch("yerbabuena").pk, self.branch.pk)

    def test_inventory_product_ids_are_not_inventory_ids(self):
        # El código solicitado identifica un producto, no el registro de inventario.
        unrelated = Inventario.objects.create(productoid=self.other, sucursalid=self.branch, cantidad=9)
        text = bot.tool_inventory(self.profile, {"consulta": self.product.codigo_de_barras})
        self.assertIn(self.product.nombre, text)
        self.assertNotIn(self.other.nombre, text)

    def test_payment_name_filter_includes_every_payment_not_only_first_fifty(self):
        Egreso.objects.bulk_create([Egreso(concepto=self.concept, monto=1, medio_pago="efectivo", registrado_por_nombre="William Nova") for _ in range(100)])
        text = bot.tool_expenses(self.profile, {"concepto": "cocacola", "usuario": "wlliam nova"})
        self.assertIn("$1.100,25", text)
        self.assertEqual(Egreso.objects.count(), 101)

    def test_catalog_name_match_only_prepares_change(self):
        reply = bot._execute_tool(self.profile, "preparar_cambio_catalogo", {
            "entidad": "producto", "operacion": "editar", "registro": "aroz diana", "campos": [{"campo": "precio", "valor": "1200.25"}],
        })
        self.product.refresh_from_db()
        self.assertEqual(self.product.precio, Decimal("1000.25"))
        self.assertIn(self.product.nombre, reply.text)
        self.assertIn("Confirmar cambio", reply.text)
        self.assertEqual(TelegramAccionPendiente.objects.get().argumentos["registro_id"], self.product.pk)

    def test_ambiguous_product_change_does_not_prepare_anything(self):
        Producto.objects.create(nombre="ARROZ DIANA 1000 G", precio=2000, categoria=self.category)
        with self.assertRaisesMessage(bot.TelegramBotError, "varias opciones"):
            bot._execute_tool(self.profile, "preparar_cambio_catalogo", {"entidad": "producto", "operacion": "editar", "registro": "aroz diana", "campos": [{"campo": "precio", "valor": "3000"}]})
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_return_matches_only_products_in_that_sale_and_requires_quantity(self):
        selected = _select_lines([self.detail], [{"producto": "aroz diana", "cantidad": 1}])
        self.assertEqual(selected[0]["detalle"].pk, self.detail.pk)
        with self.assertRaises(bot.TelegramBotError):
            _select_lines([self.detail], [{"producto": "lenteja", "cantidad": 1}])
        with self.assertRaises(bot.TelegramBotError):
            _select_lines([self.detail], [{"producto": "arroz diana"}])

    def test_similar_payment_method_uses_real_active_code(self):
        self.assertEqual(bot._resolve_payment_method("efectibo", active_only=True), "efectivo")

    def test_ambiguous_name_stays_in_chat_context_without_creating_action(self):
        Producto.objects.create(nombre="ARROZ DIANA 1000 G", precio=2000, categoria=self.category)
        message = TelegramActualizacion.objects.create(update_id=990001, telegram_user_id=9931, telegram_chat_id=9931, tipo="TEXTO", texto="Cambia el precio del aroz diana a 3000")
        args = {"entidad": "producto", "operacion": "editar", "registro": "aroz diana", "campos": [{"campo": "precio", "valor": "3000"}]}
        with patch.object(bot, "_intelligent_function_call", return_value=("preparar_cambio_catalogo", args, "")):
            reply = bot.build_reply(message, self.client_stub)
        self.assertEqual(reply.intent, "aclarar_nombre")
        self.assertIn("¿Cuál quieres usar?", reply.text)
        self.assertFalse(TelegramAccionPendiente.objects.exists())
        message.estado, message.respuesta, message.procesado_en = "PROCESADO", reply.text, timezone.now()
        message.save(update_fields=["estado", "respuesta", "procesado_en"])
        following = TelegramActualizacion.objects.create(update_id=990002, telegram_user_id=9931, telegram_chat_id=9931, tipo="TEXTO", texto=f"El ID {self.product.pk}")
        with patch.object(bot, "_intelligent_function_call", return_value=("", {}, "De acuerdo")) as ai:
            bot.build_reply(following, self.client_stub)
        history = ai.call_args.kwargs["history"]
        self.assertIn(message.texto, [item["text"] for item in history])
        self.assertIn(reply.text, [item["text"] for item in history])
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_quantity_sum_for_named_product_grouped_by_branch(self):
        result = self.query("productos_vendidos", operacion="sumar", campo="cantidad", filtros=[{"campo": "producto", "operador": "igual", "valor": "aroz diana"}], agrupar=["sucursal"])
        for text in (self.product.nombre, "Yerbabuena", "Total: 3", "unidad registrada"):
            self.assertIn(text, result.text)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.total, Decimal("3000.75"))

    def test_sale_line_total_is_precise_and_distinguished_from_net_sales(self):
        text = self.query("productos_vendidos", operacion="sumar", campo="importe").text
        self.assertIn("$3.000,75", text)
        self.assertIn("sin descontar descuentos globales", text)

    def test_multiple_filters_apply_before_aggregation(self):
        Egreso.objects.create(concepto=self.concept, monto=2500, medio_pago="nequi", registrado_por_nombre="Otra persona")
        result = self.query(operacion="sumar", campo="importe", filtros=[
            {"campo": "importe", "operador": "al_menos", "valor": "1000"},
            {"campo": "usuario", "operador": "igual", "valor": "wlliam nova"},
        ])
        self.assertIn("Total: $1.000,25", result.text)
        self.assertNotIn("$3.500,25", result.text)

    def test_method_aliases_are_combined_in_grouped_sums(self):
        Egreso.objects.create(concepto=self.concept, monto=2000, medio_pago="tarjeta", registrado_por_nombre="William Nova")
        Egreso.objects.create(concepto=self.concept, monto=3000, medio_pago="caja_social", registrado_por_nombre="William Nova")
        result = self.query(operacion="sumar", campo="importe", agrupar=["medio_pago"])
        self.assertIn("$5.000", result.text)
        self.assertIn("2 grupos", result.text)

    def test_distinct_count_does_not_count_same_employee_twice(self):
        Venta.objects.create(fecha=timezone.localdate(), hora="13:00", empleadoid=self.employee, sucursalid=self.branch, puntopagoid=self.point, total=1000, mediopago="efectivo")
        result = self.query("ventas", operacion="contar", campo="empleado")
        self.assertIn("valores distintos: 1", result.text)
        self.assertIn("2 registros", result.text)

    def test_average_minimum_and_maximum(self):
        Egreso.objects.create(concepto=self.concept, monto="2000.75", medio_pago="efectivo", registrado_por_nombre="William Nova")
        for operation, expected in (("promedio", "$1.500,50"), ("minimo", "$1.000,25"), ("maximo", "$2.000,75")):
            with self.subTest(operation=operation):
                self.assertIn(expected, self.query(operacion=operation, campo="importe").text)

    def test_empty_average_is_not_invented_zero(self):
        result = self.query(operacion="promedio", campo="importe", filtros=[{"campo": "importe", "operador": "mayor", "valor": "999999"}])
        self.assertIn("Promedio: Sin datos", result.text)

    def test_event_queries_default_to_today_and_date_ranges_are_bounded(self):
        Egreso.objects.filter(pk=self.expense.pk).update(creado_en=timezone.now() - timedelta(days=2))
        self.assertIn("Total: $0", self.query(operacion="sumar", campo="importe").text)
        with self.assertRaisesMessage(bot.TelegramBotError, "un año"):
            self.query(desde="2020-01-01", hasta="2026-01-01")
        with self.assertRaisesMessage(bot.TelegramBotError, "actuales"):
            self.query("inventario", desde="2020-01-01")

    def test_query_pages_keep_filters_and_audit(self):
        Egreso.objects.bulk_create([Egreso(concepto=self.concept, monto=10, medio_pago="efectivo", registrado_por_nombre="William Nova") for _ in range(6)])
        result = self.query(operacion="listar", filtros=[{"campo": "importe", "operador": "hasta", "valor": "10"}])
        callback = result.reply_markup["inline_keyboard"][-1][-1]["callback_data"]
        second = bot._handle_callback(SimpleNamespace(texto=callback, callback_query_id="q-page"), self.profile, self.client_stub)
        self.assertIn("página 2 de 2", second.text)
        self.assertEqual(second.pagination["arguments"]["filtros"][0]["valor"], "10")
        self.assertTrue(TelegramAuditoria.objects.filter(accion="consultar_datos", exitoso=True).exists())

    def test_query_can_continue_with_new_date_and_two_groups(self):
        self.query(operacion="sumar", campo="importe")
        result = bot._execute_tool(self.profile, "continuar_consulta", {"cambios": {"grupos": ["concepto", "usuario"]}})
        self.assertIn("COCA-COLA", result.text)
        self.assertIn("William Nova", result.text)
        self.assertIn("$1.000,25", result.text)

    def test_complex_query_combines_with_existing_read_tools(self):
        reply = bot._execute_tool(self.profile, "consultar_varias", {"consultas": [
            {"herramienta": "consultar_datos", "argumentos_json": json.dumps({"fuente": "productos_vendidos", "operacion": "sumar", "campo": "cantidad"})},
            {"herramienta": "consultar_pagos", "argumentos_json": "{}"},
        ]})
        self.assertIn("Total: 3", reply.text)
        self.assertIn("$1.000,25", reply.text)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_source_fields_operations_and_orm_paths_are_allowlisted(self):
        requests = [
            {"fuente": "Usuario"}, {"fuente": "pagos", "sql": "DROP TABLE egresos"},
            {"fuente": "pagos", "operacion": "delete"},
            {"fuente": "pagos", "filtros": [{"campo": "registrado_por__password", "operador": "igual", "valor": "x"}]},
            {"fuente": "pagos", "ordenar": "registrado_por__password"},
            {"fuente": "pagos", "agrupar": ["password"], "operacion": "contar"},
            {"fuente": "productos", "campo": "nombre", "operacion": "sumar"},
        ]
        for args in requests:
            with self.subTest(args=args), self.assertRaises(bot.TelegramBotError):
                bot._execute_tool(self.profile, "consultar_datos", args)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(Usuario.objects.count(), 1)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_bad_dates_numbers_and_too_many_filters_are_rejected(self):
        cases = [
            {"filtros": [{"campo": "importe", "operador": "mayor", "valor": "NaN"}]},
            {"filtros": [{"campo": "importe", "operador": "mayor", "valor": "Infinity"}]},
            {"filtros": [{"campo": "fecha", "operador": "igual", "valor": "2026-02-31"}]},
            {"filtros": [{"campo": "importe", "operador": "igual", "valor": "1"}] * 9},
            {"operacion": "contar", "agrupar": ["concepto", "usuario", "fecha"]},
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(bot.TelegramBotError):
                self.query(**args)

    def test_sql_like_filter_is_only_literal_data(self):
        text = self.query("productos", filtros=[{"campo": "nombre", "operador": "contiene", "valor": "' OR 1=1; DROP TABLE productos --"}]).text
        self.assertIn("0 registros", text)
        self.assertEqual(Producto.objects.count(), 2)

    def test_permission_is_checked_before_reading_data(self):
        self.user.is_active = False
        with self.assertNumQueries(0), self.assertRaises(PermissionDenied):
            queries.tool_query(self.profile, {"fuente": "pagos", "operacion": "contar"})

    def test_sales_scope_and_entity_name_lookup_do_not_leak_other_branches(self):
        remote = Sucursal.objects.create(nombre="Sucursal secreta", telefono="3000000000")
        point = PuntosPago.objects.create(nombre="Caja secreta", sucursalid=remote)
        Venta.objects.create(fecha=timezone.localdate(), hora="12:00", empleadoid=self.employee, sucursalid=remote, puntopagoid=point, total=9999, mediopago="nequi")
        self.user.rolid = Rol.objects.create(nombre="Cajero")
        self.user.save(update_fields=["rolid"])
        Empleado.objects.filter(pk=self.employee.pk).update(usuarioid=self.user)
        with patch.object(bot, "user_can_access_url_name", return_value=True):
            text = self.query("ventas", operacion="sumar", campo="importe", filtros=[{"campo": "sucursal", "operador": "igual", "valor": str(remote.pk)}]).text
        self.assertNotIn("Sucursal secreta", text)
        self.assertNotIn("$9.999", text)

    def test_text_and_transcribed_audio_can_route_to_the_new_tool(self):
        for kind in ("TEXTO", "VOZ"):
            message = TelegramActualizacion.objects.create(update_id=99400 + TelegramActualizacion.objects.count(), telegram_user_id=9931, telegram_chat_id=9931, tipo=kind, texto="Analiza el arroz vendido", transcripcion="Analiza el arroz vendido" if kind == "VOZ" else "")
            with patch.object(bot, "_intelligent_function_call", return_value=("consultar_datos", {"fuente": "productos_vendidos", "operacion": "sumar", "campo": "cantidad"}, "")):
                self.assertIn("Total: 3", bot.build_reply(message, self.client_stub).text)
