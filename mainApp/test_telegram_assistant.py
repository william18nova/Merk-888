import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from mainApp.models import (
    Categoria, Cliente, ConceptoEgreso, DetalleVenta, Egreso, Empleado, Inventario,
    Producto, PuntosPago, Rol, Sucursal, TelegramAccionPendiente,
    TelegramActualizacion, TelegramAuditoria, TelegramUsuario, Usuario, Venta,
)
from mainApp.services import telegram_assistant as assistant
from mainApp.services import telegram_bot as bot


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True, TELEGRAM_BOT_TOKEN="123:test-only")
class TelegramAssistantTests(TestCase):
    def setUp(self):
        self.role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("Dueño pruebas", rolid=self.role)
        self.profile = TelegramUsuario.objects.create(usuario=self.user, telegram_user_id=880, telegram_chat_id=880)
        self.client_stub = SimpleNamespace(answer_callback=MagicMock())
        self.branch = Sucursal.objects.create(nombre="Centro")
        self.point = PuntosPago.objects.create(nombre="Caja principal", sucursalid=self.branch)
        self.category = Categoria.objects.create(nombre="BEBIDAS")
        self.product = Producto.objects.create(nombre="AGUA", precio="100.50", categoria=self.category)
        Empleado.objects.bulk_create([
            Empleado(nombre="Ana", apellido="Perez", sucursalid=self.branch, numerodocumento="100000", puesto="Cajera", email="ana@example.test", telefono="3001111111"),
            Empleado(nombre="Luis", apellido="Perez", sucursalid=self.branch, numerodocumento="200000", puesto="Cajero", email="luis@example.test", telefono="3002222222"),
        ])
        self.ana, self.luis = list(Empleado.objects.order_by("pk"))
        self.concept = ConceptoEgreso.objects.create(nombre="COCA-COLA")

    def query(self, name="consultar_informe", **args):
        return bot._execute_tool(self.profile, name, args)

    def sale(self, employee=None, total="100.50", day=None):
        sale = Venta.objects.create(fecha=day or timezone.localdate(), hora="12:00", empleadoid=employee or self.ana, sucursalid=self.branch, puntopagoid=self.point, total=total, mediopago="efectivo")
        # Varias líneas no deben duplicar el total de la venta al agrupar empleados.
        for _ in range(2):
            DetalleVenta.objects.create(ventaid=sale, productoid=self.product, cantidad=1, preciounitario="50.25")
        return sale

    def expense(self, amount="20.25", method="efectivo", at=None):
        expense = Egreso.objects.create(concepto=self.concept, monto=amount, medio_pago=method, registrado_por=self.user, registrado_por_nombre=self.user.nombreusuario)
        # auto_now_add ignora la fecha pasada al crear. Fijarla después permite
        # probar límites de zona horaria sin depender del día de ejecución.
        if at is not None:
            Egreso.objects.filter(pk=expense.pk).update(creado_en=at)
            expense.refresh_from_db()
        return expense

    def message(self, text, voice=False):
        return TelegramActualizacion.objects.create(update_id=10000 + TelegramActualizacion.objects.count(), telegram_user_id=880, telegram_chat_id=880, tipo="VOZ" if voice else "TEXTO", texto="" if voice else text, transcripcion=text if voice else "")

    def callback(self, reply, label):
        button = next(item for row in reply.reply_markup["inline_keyboard"] for item in row if label in item["text"])
        return bot._handle_callback(SimpleNamespace(texto=button["callback_data"], callback_query_id="test-assistant"), self.profile, self.client_stub)

    def test_employee_ranking_uses_sale_totals_not_joined_lines(self):
        self.sale(total="100.50")
        self.sale(total="200.50")
        self.sale(self.luis, total="250.25")
        reply = self.query(fuente="ventas", agrupar="empleado", incluir_total=True, incluir_cantidad=True, incluir_promedio=True)
        self.assertIn("Total: $551,25", reply.text)
        self.assertIn("3 ventas", reply.text)
        self.assertIn("Ana Perez · $301", reply.text)
        self.assertIn("promedio $150,50", reply.text)
        self.assertLess(reply.text.index("Ana Perez"), reply.text.index("Luis Perez"))
        first = self.query(fuente="ventas", agrupar="empleado", primeros=1)
        self.assertIn("Ana Perez", first.text)
        self.assertNotIn("Luis Perez", first.text)
        by_average = self.query(fuente="ventas", agrupar="empleado", orden="promedio", primeros=1)
        self.assertIn("Luis Perez", by_average.text)
        self.assertNotIn("Ana Perez", by_average.text)

    def test_report_supports_sale_groups_and_null_client(self):
        self.sale()
        for group, label in (("cliente", "Sin asignar"), ("sucursal", "Centro"), ("punto_pago", "Caja principal"), ("dia", timezone.localdate().isoformat())):
            with self.subTest(group=group):
                self.assertIn(label, self.query(fuente="ventas", agrupar=group).text)

    def test_report_comparison_uses_equal_inclusive_period_and_same_filters(self):
        today = timezone.localdate()
        self.sale(total="200", day=today)
        self.sale(total="100", day=today - timedelta(days=2))
        self.sale(self.luis, total="900", day=today - timedelta(days=2))
        reply = self.query(fuente="ventas", desde=(today - timedelta(days=1)).isoformat(), hasta=today.isoformat(), empleado=str(self.ana.pk), comparar_anterior=True)
        self.assertIn("diferencia $100", reply.text)
        self.assertIn("+100.00%", reply.text)
        self.assertIn((today - timedelta(days=3)).strftime("%d/%m/%Y"), reply.text)

    def test_zero_comparison_never_divides_by_zero(self):
        self.sale()
        reply = self.query(fuente="ventas", comparar_anterior=True)
        self.assertIn("sin base para porcentaje", reply.text)

    def test_report_employee_resolution_does_not_choose_ambiguous_name(self):
        self.sale()
        self.assertIn("$100,50", self.query(fuente="ventas", empleado="Ana Perez").text)
        with self.assertRaisesMessage(bot.TelegramBotError, "varias opciones parecidas"):
            self.query(fuente="ventas", empleado="Perez")

    def test_payment_reports_group_and_filter_canonical_methods_and_amounts(self):
        self.expense("20.25", "tarjeta")
        self.expense("30.50", "caja_social")
        self.expense("200", "efectivo")
        reply = self.query(fuente="pagos", agrupar="concepto", medio_pago="tarjeta", monto_max=100, incluir_total=True, incluir_cantidad=True)
        self.assertIn("Total: $50,75", reply.text)
        self.assertIn("COCA-COLA", reply.text)
        self.assertIn("2 pagos", reply.text)
        self.assertIn(self.user.nombreusuario, self.query(fuente="pagos", agrupar="usuario").text)

    def test_payment_day_group_uses_colombia_not_utc_day(self):
        at = datetime(2026, 9, 7, 2, 30, tzinfo=ZoneInfo("UTC"))
        self.expense(at=at)
        reply = self.query(fuente="pagos", agrupar="dia", desde="2026-09-06", hasta="2026-09-06", incluir_total=True)
        self.assertIn("2026-09-06", reply.text)
        self.assertIn("Total: $20,25", reply.text)

    def test_report_refuses_unsupported_filters_types_and_fields(self):
        cases = (
            {"fuente": "ventas", "agrupar": "concepto"}, {"fuente": "pagos", "sucursal": "Centro"},
            {"fuente": "ventas", "monto_min": 0}, {"fuente": "ventas", "sql": "select *"},
            {"fuente": "ventas", "primeros": 0}, {"fuente": "ventas", "agrupar": "empleado", "primeros": 51},
            {"fuente": "pagos", "monto_min": float("nan")}, {"fuente": "pagos", "monto_min": True},
            {"fuente": "pagos", "monto_min": 10, "monto_max": 1},
        )
        for args in cases:
            with self.subTest(args=args), self.assertRaises(bot.TelegramBotError):
                self.query(**args)

    def test_read_reports_recheck_permissions_and_inactive_user(self):
        with patch.object(bot, "user_can_access_url_name", return_value=False):
            for source in ("ventas", "pagos"):
                with self.assertRaises(PermissionDenied):
                    self.query(fuente=source)
        self.user.is_active = False
        with self.assertRaises(PermissionDenied):
            self.query("consultar_resumen_negocio")

    def test_brief_omits_unauthorized_sections_and_labels_live_data(self):
        Inventario.objects.create(productoid=self.product, sucursalid=self.branch, cantidad=0)
        with patch.object(bot, "user_can_access_url_name", side_effect=lambda user, route: route in {"home", "visualizar_inventarios"}):
            reply = self.query("consultar_resumen_negocio")
        self.assertIn("1 sin existencias", reply.text)
        self.assertIn("Inventario actual", reply.text)
        self.assertIn("Sin permiso para incluir", reply.text)
        self.assertNotIn("Total:", reply.text)
        with self.assertRaises(bot.TelegramBotError):
            self.query("consultar_resumen_negocio", secciones=["ventas", "ventas"])

    def test_multi_query_is_read_only_and_preserves_numbered_results(self):
        self.sale()
        self.expense()
        queries = [{"herramienta": name, "argumentos_json": json.dumps(args)} for name, args in (
            ("consultar_informe", {"fuente": "ventas"}), ("consultar_pagos", {}),
            ("consultar_registros", {"recurso": "productos"}),
        )]
        reply = self.query("consultar_varias", consultas=queries)
        for expected in ("Consulta 1", "Consulta 2", "Consulta 3", "$100,50", "$20,25", "AGUA"):
            self.assertIn(expected, reply.text)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(Venta.objects.count(), 1)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_multi_validates_entire_plan_before_running_any_child(self):
        good = {"herramienta": "consultar_registros", "argumentos_json": '{"recurso": "productos"}'}
        bad = (
            {"herramienta": "preparar_registro_pago", "argumentos_json": "{}"},
            {"herramienta": "consultar_varias", "argumentos_json": "{}"},
            {"herramienta": "consultar_ventas", "argumentos_json": '{"sql":"select 1"}'},
            {"herramienta": "consultar_informe", "argumentos_json": "not json"},
            {"herramienta": "consultar_pagos", "argumentos_json": '{"monto_min":NaN}'},
        )
        for query in bad:
            with self.subTest(query=query), self.assertRaises(bot.TelegramBotError):
                self.query("consultar_varias", consultas=[good, query])
        self.assertFalse(TelegramAuditoria.objects.filter(accion="consultar_registros").exists())
        for queries in ([], [good] * 5):
            with self.assertRaises(bot.TelegramBotError):
                self.query("consultar_varias", consultas=queries)

    def test_multi_reports_permission_failure_without_exposing_other_data(self):
        self.sale()
        with patch.object(bot, "user_can_access_url_name", side_effect=lambda user, route: route == "visualizar_productos"):
            reply = self.query("consultar_varias", consultas=[
                {"herramienta": "consultar_informe", "argumentos_json": '{"fuente":"ventas"}'},
                {"herramienta": "consultar_registros", "argumentos_json": '{"recurso":"productos"}'},
            ])
        self.assertIn("no completada", reply.text)
        self.assertIn("AGUA", reply.text)
        self.assertNotIn("Informe de ventas", reply.text)

    def test_continuation_retains_filters_but_resets_page_and_anchors_dates(self):
        yesterday = timezone.localdate() - timedelta(days=1)
        self.query(fuente="ventas", agrupar="empleado", sucursal="Centro")
        reply = self.query("continuar_consulta", cambios={"desde": yesterday.isoformat()})
        audit = TelegramAuditoria.objects.filter(accion="consultar_informe", exitoso=True).first()
        self.assertEqual(audit.argumentos["sucursal"], str(self.branch.pk))
        self.assertEqual(audit.argumentos["hasta"], yesterday.isoformat())
        self.assertEqual(reply.intent, "consultar_informe")

    def test_continuation_can_group_previous_sales_and_payments(self):
        self.sale()
        self.query("consultar_ventas")
        self.assertIn("Ana Perez", self.query("continuar_consulta", cambios={"agrupar": "empleado"}).text)
        self.expense("20")
        self.expense("200")
        self.query("consultar_pagos", detalle=True, monto_max=100)
        reply = self.query("continuar_consulta", cambios={"agrupar": "concepto"})
        self.assertIn("COCA-COLA · $20", reply.text)
        self.assertIn("COCA-COLA", reply.text)

    def test_continuation_refuses_cross_chat_stale_missing_and_write_actions(self):
        with self.assertRaises(bot.TelegramBotError):
            self.query("continuar_consulta")
        audit = bot._audit(self.profile, "consultar_informe", {"fuente": "ventas"})
        audit.telegram_chat_id = 999
        audit.save(update_fields=["telegram_chat_id"])
        with self.assertRaises(bot.TelegramBotError):
            self.query("continuar_consulta")
        audit.telegram_chat_id = 880
        audit.creado_en = timezone.now() - timedelta(hours=25)
        audit.save(update_fields=["telegram_chat_id", "creado_en"])
        bot._audit(self.profile, "confirmar_registro_pago", {})
        with self.assertRaises(bot.TelegramBotError):
            self.query("continuar_consulta")
        with self.assertRaises(bot.TelegramBotError):
            self.query("continuar_consulta", herramienta="preparar_registro_pago")

    def test_continuation_never_drops_unsupported_filter_or_bypasses_permission(self):
        self.query(fuente="ventas")
        with self.assertRaises(bot.TelegramBotError):
            self.query("continuar_consulta", cambios={"stock_max": 0})
        with patch.object(bot, "user_can_access_url_name", side_effect=lambda user, route: route == "home"):
            with self.assertRaises(PermissionDenied):
                self.query("continuar_consulta")

    def test_summary_cannot_be_paginated_as_if_it_were_a_list(self):
        self.query(fuente="ventas")
        with self.assertRaisesMessage(bot.TelegramBotError, "no tiene páginas"):
            self.query("continuar_consulta", navegacion="siguiente")
        with self.assertRaises(bot.TelegramBotError):
            self.query(fuente="ventas", pagina=2)

    def test_multi_list_navigation_retains_its_own_query(self):
        Categoria.objects.bulk_create([Categoria(nombre=f"CAT {index}") for index in range(7)])
        result = self.query("consultar_varias", consultas=[
            {"herramienta": "consultar_registros", "argumentos_json": '{"recurso":"categorias", "consulta":"CAT"}'},
            {"herramienta": "consultar_informe", "argumentos_json": '{"fuente":"ventas"}'},
        ])
        self.assertIn("1 · Siguiente", str(result.reply_markup))
        second = self.callback(result, "Siguiente")
        self.assertIn("página 2 de 2", second.text)
        self.assertIn("búsqueda: CAT", second.text)

    def test_page_buttons_cannot_replay_in_a_different_chat(self):
        Categoria.objects.bulk_create([Categoria(nombre=f"CAT {index}") for index in range(7)])
        result = self.query("consultar_registros", recurso="categorias")
        self.profile.telegram_chat_id = 999
        self.assertEqual(self.callback(result, "Siguiente").intent, "pagina_invalida")

    def test_continuation_after_multiple_queries_requires_selection(self):
        self.query("consultar_varias", consultas=[
            {"herramienta": "consultar_informe", "argumentos_json": '{"fuente":"ventas"}'},
            {"herramienta": "consultar_pagos", "argumentos_json": "{}"},
        ])
        with self.assertRaisesMessage(bot.TelegramBotError, "varias consultas"):
            self.query("continuar_consulta")
        self.assertIn("No encontré pagos registrados", self.query("continuar_consulta", herramienta="consultar_pagos").text)

    def test_report_pagination_and_followup_do_not_lose_original_scope(self):
        for index in range(7):
            client = Cliente.objects.create(nombre=f"Cliente{index}", numerodocumento=str(index))
            sale = self.sale()
            sale.clienteid = client
            sale.save(update_fields=["clienteid"])
        first = self.query(fuente="ventas", agrupar="cliente")
        second = self.callback(first, "Siguiente")
        self.assertIn("página 2 de 2", second.text)
        self.assertIn("página 1 de 2", self.query("continuar_consulta", navegacion="anterior").text)

    def test_pending_list_is_private_excludes_expired_and_cannot_confirm(self):
        for index in range(7):
            TelegramAccionPendiente.objects.create(telegram_usuario=self.profile, accion="pago_operativo", resumen=f"Propuesta {index}", vence_en=timezone.now() + timedelta(minutes=5))
        TelegramAccionPendiente.objects.create(telegram_usuario=self.profile, accion="pago_operativo", resumen="VENCIDA", vence_en=timezone.now() - timedelta(minutes=1))
        other_user = Usuario.objects.create_user("Otra cuenta", rolid=self.role)
        other = TelegramUsuario.objects.create(usuario=other_user, telegram_user_id=881, telegram_chat_id=881)
        TelegramAccionPendiente.objects.create(telegram_usuario=other, accion="pago_operativo", resumen="SECRETO OTRA CUENTA", vence_en=timezone.now() + timedelta(minutes=5))
        reply = self.query("consultar_pendientes")
        self.assertIn("7 propuestas pendientes", reply.text)
        self.assertNotIn("VENCIDA", reply.text)
        self.assertNotIn("SECRETO", reply.text)
        buttons = [item["callback_data"] for row in reply.reply_markup["inline_keyboard"] for item in row]
        self.assertTrue(any(item.startswith("page:") for item in buttons))
        self.assertTrue(any(item.startswith("cancel:") for item in buttons))
        self.assertFalse(any(item.startswith("confirm:") for item in buttons))
        self.assertIn("página 2 de 2", self.callback(reply, "Siguiente").text)

    def test_direct_commands_and_common_voice_questions_do_not_require_ai(self):
        self.sale()
        with patch.object(bot, "_intelligent_function_call", side_effect=AssertionError("No debe llamar IA")):
            for text in ("/resumen", "/ranking empleados", "/pendientes", "Jarvis, quién vendió más hoy?", "¿Y ayer?"):
                # Asegurar que 'ayer' continúa un informe, no el listado de pendientes.
                if text == "¿Y ayer?":
                    self.query(fuente="ventas")
                reply = bot.build_reply(self.message(text, voice=True), self.client_stub)
                self.assertTrue(reply.text)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_long_responses_are_complete_and_buttons_only_on_last_chunk(self):
        text = "Texto de prueba 🛒\n" * 800
        keyboard = {"inline_keyboard": [[{"text": "Siguiente", "callback_data": "page:1:2"}]]}
        client = bot.TelegramApiClient()
        with patch.object(client, "_post", return_value={}) as send:
            client.send_message(880, text, reply_markup=keyboard)
        payloads = [call.kwargs["payload"] for call in send.call_args_list]
        self.assertGreater(len(payloads), 1)
        self.assertEqual("".join(payload["text"] for payload in payloads), text.strip())
        self.assertTrue(all(len(payload["text"].encode("utf-16-le")) // 2 <= 3800 for payload in payloads))
        self.assertTrue(all("reply_markup" not in payload for payload in payloads[:-1]))
        self.assertEqual(payloads[-1]["reply_markup"], keyboard)

    def employee_proposal(self, **overrides):
        user = Usuario.objects.create_user("empleado nuevo", rolid=self.role)
        data = {"nombre": "Maria", "apellido": "Gomez", "numerodocumento": "12345678", "telefono": "3001234567", "email": "maria@example.test", "puesto": "Cajera", "usuarioid": str(user.pk), "sucursalid": str(self.branch.pk)}
        data.update(overrides)
        return self.query("preparar_cambio_catalogo", entidad="empleado", operacion="crear", campos=[{"campo": key, "valor": value} for key, value in data.items()])

    def test_employee_create_needs_confirmation_reuses_form_and_syncs_client_once(self):
        reply = self.employee_proposal()
        self.assertFalse(Empleado.objects.filter(nombre="Maria").exists())
        self.assertFalse(Cliente.objects.filter(numerodocumento="12345678").exists())
        self.assertIn("ficha de cliente", reply.text)
        self.assertIn("empleado nuevo", reply.text)
        self.assertIn("Listo, guardé", self.callback(reply, "Confirmar").text)
        self.assertEqual(Empleado.objects.filter(nombre="Maria").count(), 1)
        self.assertEqual(Cliente.objects.filter(numerodocumento="12345678").count(), 1)
        self.callback(reply, "Confirmar")
        self.assertEqual(Empleado.objects.filter(nombre="Maria").count(), 1)

    def test_employee_refuses_invalid_account_identity_and_sensitive_fields(self):
        with self.assertRaises(bot.TelegramBotError):
            self.employee_proposal(usuarioid="999999")
        with self.assertRaises(bot.TelegramBotError):
            self.query("preparar_cambio_catalogo", entidad="empleado", operacion="crear", campos=[{"campo": "password", "valor": "NO"}])
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_employee_link_permission_rechecked_at_confirmation(self):
        reply = self.employee_proposal()
        with patch.object(bot, "user_can_access_url_name", side_effect=lambda user, route: route != "visualizar_usuarios"):
            with self.assertRaises(PermissionDenied):
                self.callback(reply, "Confirmar")
        self.assertFalse(Empleado.objects.filter(nombre="Maria").exists())

    def test_employee_client_conflict_rolls_back_everything_and_reports_error(self):
        reply = self.employee_proposal()
        for name in ("Cliente uno", "Cliente dos"):
            Cliente.objects.create(nombre=name, numerodocumento="12345678")
        response = self.callback(reply, "Confirmar")
        self.assertIn("No se guardó", response.text)
        self.assertFalse(Empleado.objects.filter(nombre="Maria").exists())
        self.assertEqual(TelegramAccionPendiente.objects.get().estado, "ERROR")
        self.assertEqual(Cliente.objects.filter(numerodocumento="12345678").count(), 2)

    def test_employee_edit_preserves_other_fields_and_syncs_client(self):
        reply = self.employee_proposal()
        self.callback(reply, "Confirmar")
        employee = Empleado.objects.get(nombre="Maria")
        edit = self.query("preparar_cambio_catalogo", entidad="empleado", operacion="editar", registro_id=employee.pk, campos=[{"campo": "telefono", "valor": "3003333333"}])
        employee.refresh_from_db()
        self.assertEqual(employee.telefono, "3001234567")
        self.callback(edit, "Confirmar")
        employee.refresh_from_db()
        self.assertEqual(employee.telefono, "3003333333")
        self.assertEqual(employee.email, "maria@example.test")
        self.assertEqual(Cliente.objects.get(numerodocumento="12345678").telefono, "3003333333")

    def test_ai_history_stays_within_same_chat_and_recent_link(self):
        old = self.message("DATOS OTRO CHAT")
        old.estado, old.telegram_chat_id = "PROCESADO", 999
        old.save(update_fields=["estado", "telegram_chat_id"])
        message = self.message("Muéstrame algo sobre el catálogo")
        with patch.object(bot, "_intelligent_function_call", return_value=("", {}, "¿Qué catálogo?")) as ai:
            bot.build_reply(message, self.client_stub)
        self.assertEqual(ai.call_args.kwargs["history"], [])

    def test_both_ai_providers_get_all_new_tools_and_explicit_employee_rank_prompt(self):
        gemini = {definition["name"] for definition in bot.GEMINI_TOOLS[0]["functionDeclarations"]}
        groq = {definition["function"]["name"] for definition in bot.GROQ_CHAT_TOOLS}
        self.assertTrue(set(assistant.TOOL_FUNCTIONS) <= gemini)
        self.assertEqual(gemini, groq)
        prompt = bot._assistant_system_prompt()
        self.assertIn("quién vendió más este mes", prompt)
        self.assertIn("consultar_varias", prompt)
        self.assertIn("no inventes contexto", prompt)
