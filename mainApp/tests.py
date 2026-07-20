from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.db import IntegrityError
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse

from .models import CambioDevolucion, TurnoCajaMedio
from .permissions import route_permission_for_url_name
from .services.employee_client import (
    EmployeeClientSyncError,
    _matching_clients,
    sync_employee_client,
)
from .views import (
    GenerarVentaView,
    NequiNotificationWebhookView,
    ProductoAutocomplete,
    _looks_like_nequi_payment,
    _parse_nequi_amount,
    _parse_nequi_sender_plain,
    _aplicar_reintegros_a_esperados,
    _reintegro_ledger_ready,
    _resolve_turno_cajero,
    _venta_nequi_status,
)


class InventoryCreateRoutingTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_create_page_uses_its_own_branch_autocomplete_permission(self):
        url = reverse("sucursal_inventario_agregar_autocomplete")

        self.assertEqual(resolve(url).url_name, "sucursal_inventario_agregar_autocomplete")
        self.assertEqual(
            route_permission_for_url_name("sucursal_inventario_agregar_autocomplete"),
            "inventarios_crear",
        )

        template = (
            settings.BASE_DIR / "mainApp" / "templates" / "agregar_inventario.html"
        ).read_text(encoding="utf-8")
        self.assertIn("{% url 'sucursal_inventario_agregar_autocomplete' %}", template)
        self.assertIn('class="container-inventario inventory-create-page"', template)
        self.assertIn("fa-cubes icon-inventario", template)
        self.assertNotIn("inventory-hero", template)
        self.assertNotIn(
            'window.sucursalAutocompleteUrl = "{% url \'sucursal_inventario_autocomplete\' %}"',
            template,
        )

    def test_product_autocomplete_rejects_non_ascii_numeric_branch_without_querying(self):
        request = self.factory.get(
            reverse("producto_inventario_autocomplete"),
            {"sucursal_id": "²", "page": "1"},
        )
        request.user = SimpleNamespace(is_authenticated=True)

        response = ProductoAutocomplete().get(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload, {"results": [], "has_more": False, "total": 0})


class TurnoCajaAutofillTests(SimpleTestCase):
    @patch("mainApp.views.Usuario.objects.filter")
    def test_resolves_autofilled_cashier_name_without_hidden_id(self, filter_users):
        cashier = SimpleNamespace(pk=1, nombreusuario="William Nova")
        filter_users.return_value.first.return_value = cashier

        resolved, error = _resolve_turno_cajero("", "William Nova")

        self.assertIs(resolved, cashier)
        self.assertIsNone(error)
        filter_users.assert_called_once_with(nombreusuario="William Nova")

    @patch("mainApp.views.Usuario.objects.filter")
    def test_rejects_mismatch_between_hidden_id_and_visible_cashier(self, filter_users):
        filter_users.return_value.first.return_value = SimpleNamespace(
            pk=1,
            nombreusuario="William Nova",
        )

        resolved, error = _resolve_turno_cajero("1", "Otro usuario")

        self.assertIsNone(resolved)
        self.assertIn("no coincide", error)

    def test_turn_button_supports_password_manager_autofill(self):
        base_dir = settings.BASE_DIR / "mainApp"
        template = (base_dir / "templates" / "turno_caja.html").read_text(
            encoding="utf-8"
        )
        script = (
            base_dir / "static" / "javascript" / "turno_caja.js"
        ).read_text(encoding="utf-8")

        self.assertIn('name="username"', template)
        self.assertIn('autocomplete="username"', template)
        self.assertIn('value="{{ request.user.nombreusuario }}"', template)
        self.assertIn('id="cajero_id" value="{{ request.user.pk }}"', template)
        self.assertIn('name="password"', template)
        self.assertIn("turno_caja.js' %}?v=18", template)
        self.assertIn("btnIniciar.disabled = inflightAction", script)
        self.assertIn("cajero_nombre", script)


class NequiWebhookParsingTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_reads_plain_json_body_without_json_content_type(self):
        request = self.factory.post(
            "/api/macrodroid/nequi/",
            data='{"not_title":"Pago recibido","not_text":"Ana Ruiz te envio $10.000"}',
            content_type="text/plain",
        )

        payload = NequiNotificationWebhookView()._payload(request)

        self.assertEqual(payload["not_title"], "Pago recibido")
        self.assertEqual(payload["not_text"], "Ana Ruiz te envio $10.000")

    def test_detects_nequi_payment_text_even_without_app_name(self):
        text = "Ana Ruiz te envio $10.000"
        amount = _parse_nequi_amount(text)

        self.assertEqual(amount, Decimal("10000.00"))
        self.assertTrue(_looks_like_nequi_payment("", text, amount))
        self.assertEqual(_parse_nequi_sender_plain(text), "Ana Ruiz")


class VentaNequiLinkStatusTests(SimpleTestCase):
    def test_status_distinguishes_linked_unlinked_and_non_nequi_sales(self):
        notification = SimpleNamespace(pk=321)

        self.assertEqual(
            GenerarVentaView._nequi_sale_status(Decimal("10000"), notification),
            {
                "nequi_payment": True,
                "nequi_linked": True,
                "nequi_notification_id": 321,
            },
        )
        self.assertEqual(
            GenerarVentaView._nequi_sale_status(Decimal("10000"), None),
            {
                "nequi_payment": True,
                "nequi_linked": False,
                "nequi_notification_id": None,
            },
        )
        self.assertEqual(
            GenerarVentaView._nequi_sale_status(Decimal("0"), notification),
            {
                "nequi_payment": False,
                "nequi_linked": False,
                "nequi_notification_id": None,
            },
        )

    def test_success_alert_uses_server_confirmed_nequi_status(self):
        script = (
            settings.BASE_DIR
            / "mainApp"
            / "static"
            / "javascript"
            / "generar_venta.js"
        ).read_text(encoding="utf-8")

        self.assertIn("function buildSaleSuccessMessage", script)
        self.assertIn("response.nequi_payment", script)
        self.assertIn("response.nequi_linked", script)
        self.assertIn("Pago Nequi:", script)
        self.assertIn("NO VINCULADO", script)


class VentaListNequiStatusTests(SimpleTestCase):
    def test_status_supports_linked_unlinked_mixed_and_legacy_sales(self):
        cases = [
            (
                ("mixto", True, True, 801),
                {
                    "nequi_payment": True,
                    "nequi_linked": True,
                    "nequi_notification_id": 801,
                },
            ),
            (
                ("mixto", True, True, None),
                {
                    "nequi_payment": True,
                    "nequi_linked": False,
                    "nequi_notification_id": None,
                },
            ),
            (
                ("nequi", False, False, None),
                {
                    "nequi_payment": True,
                    "nequi_linked": False,
                    "nequi_notification_id": None,
                },
            ),
            (
                ("efectivo", True, False, 802),
                {
                    "nequi_payment": False,
                    "nequi_linked": False,
                    "nequi_notification_id": None,
                },
            ),
        ]

        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(_venta_nequi_status(*args), expected)

    def test_list_and_detail_render_nequi_link_status(self):
        base_dir = settings.BASE_DIR / "mainApp"
        script = (
            base_dir / "static" / "javascript" / "visualizar_ventas.js"
        ).read_text(encoding="utf-8")
        detail = (base_dir / "templates" / "ver_venta.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("function renderMedioPago", script)
        self.assertIn("row.nequi_payment", script)
        self.assertIn("row.nequi_linked", script)
        self.assertIn("Nequi no vinculado", script)
        self.assertIn("nequi-detail-status--linked", detail)
        self.assertIn("nequi-detail-status--unlinked", detail)

    def test_sales_list_exposes_linked_and_unlinked_nequi_filters(self):
        base_dir = settings.BASE_DIR / "mainApp"
        template = (
            base_dir / "templates" / "visualizar_ventas.html"
        ).read_text(encoding="utf-8")
        script = (
            base_dir / "static" / "javascript" / "visualizar_ventas.js"
        ).read_text(encoding="utf-8")
        view_source = (base_dir / "views.py").read_text(encoding="utf-8")

        self.assertIn('id="filtro-nequi-status"', template)
        self.assertIn('<option value="linked">Vinculados</option>', template)
        self.assertIn('<option value="unlinked">No vinculados</option>', template)
        self.assertIn('nequi_status: $("#filtro-nequi-status")', script)
        self.assertIn('nequi_status == "linked"', view_source)
        self.assertIn('nequi_status == "unlinked"', view_source)


class EmployeeClientSyncTests(SimpleTestCase):
    def _employee(self, document="12345678"):
        return SimpleNamespace(
            numerodocumento=document,
            nombre="Ana",
            apellido="Ruiz",
            telefono="3001234567",
            email="ana@example.com",
        )

    @patch("mainApp.services.employee_client.Cliente.objects.create")
    @patch("mainApp.services.employee_client._matching_clients", return_value=[])
    def test_creates_client_with_employee_identity(self, _matches, create):
        created_client = SimpleNamespace(pk=91)
        create.return_value = created_client

        client, was_created = sync_employee_client(self._employee())

        self.assertIs(client, created_client)
        self.assertTrue(was_created)
        create.assert_called_once_with(
            numerodocumento="12345678",
            nombre="Ana",
            apellido="Ruiz",
            telefono="3001234567",
            email="ana@example.com",
        )

    @patch("mainApp.services.employee_client._matching_clients")
    def test_reuses_and_updates_existing_client(self, matches):
        client = MagicMock(
            pk=92,
            numerodocumento="12345678",
            nombre="Nombre viejo",
            apellido="Ruiz",
            telefono="3001234567",
            email="viejo@example.com",
        )
        matches.return_value = [client]

        synced, was_created = sync_employee_client(self._employee())

        self.assertIs(synced, client)
        self.assertFalse(was_created)
        self.assertEqual(client.nombre, "Ana")
        self.assertEqual(client.email, "ana@example.com")
        client.save.assert_called_once()

    @patch("mainApp.services.employee_client._matching_clients")
    def test_document_change_moves_previous_client_when_destination_is_free(self, matches):
        previous = MagicMock(
            pk=93,
            numerodocumento="12345678",
            nombre="Ana",
            apellido="Ruiz",
            telefono="3001234567",
            email="ana@example.com",
        )
        matches.side_effect = [[], [previous]]

        synced, was_created = sync_employee_client(
            self._employee("87654321"),
            previous_document="12345678",
        )

        self.assertIs(synced, previous)
        self.assertFalse(was_created)
        self.assertEqual(previous.numerodocumento, "87654321")

    @patch("mainApp.services.employee_client._matching_clients")
    def test_rejects_ambiguous_duplicate_clients(self, matches):
        matches.return_value = [SimpleNamespace(pk=1), SimpleNamespace(pk=2)]

        with self.assertRaises(EmployeeClientSyncError):
            sync_employee_client(self._employee())

    @patch("mainApp.services.employee_client.Cliente.objects.only")
    @patch("mainApp.services.employee_client.Cliente.objects.filter")
    def test_matching_clients_detects_exact_and_formatted_duplicates(
        self,
        filter_clients,
        only_clients,
    ):
        exact = SimpleNamespace(pk=1, numerodocumento="12345678")
        formatted = SimpleNamespace(pk=2, numerodocumento="12.345.678")
        filter_clients.return_value.order_by.return_value.__getitem__.return_value = [
            exact
        ]
        (
            only_clients.return_value
            .exclude.return_value
            .order_by.return_value
            .iterator.return_value
        ) = [formatted]

        self.assertEqual(_matching_clients("12345678"), [exact, formatted])

    def test_employee_model_and_migration_keep_client_invariant(self):
        base_dir = settings.BASE_DIR / "mainApp"
        model_source = (base_dir / "models.py").read_text(encoding="utf-8")
        migration_source = (
            base_dir / "migrations" / "0020_sync_employees_as_clients.py"
        ).read_text(encoding="utf-8")

        self.assertIn("sync_employee_client(", model_source)
        self.assertIn("with transaction.atomic():", model_source)
        self.assertIn("sync_existing_employees_as_clients", migration_source)


class EmployeeDiscountAuthorizationTests(SimpleTestCase):
    def _buyer(
        self,
        *,
        pk=71,
        document="12345678",
        password_valid=True,
        role_name="Cajero",
    ):
        user = MagicMock()
        user.check_password.return_value = password_valid
        user.rolid = SimpleNamespace(nombre=role_name) if role_name else None
        return SimpleNamespace(
            pk=pk,
            numerodocumento=document,
            usuarioid=user,
        )

    @patch.object(GenerarVentaView, "_empleado_por_documento_cliente")
    def test_employee_client_gets_ten_percent_after_password_validation(
        self,
        employee_lookup,
    ):
        buyer = self._buyer()
        employee_lookup.return_value = buyer
        cashier = SimpleNamespace(empleado=None)

        authorized = GenerarVentaView._validar_compra_empleado(
            cajero_user=cashier,
            cliente=SimpleNamespace(numerodocumento="12345678"),
            empleado_password="clave-correcta",
        )

        self.assertIs(authorized, buyer)
        buyer.usuarioid.check_password.assert_called_once_with("clave-correcta")
        self.assertEqual(
            GenerarVentaView.EMPLOYEE_DISCOUNT_RATE,
            Decimal("0.10"),
        )

    @patch.object(GenerarVentaView, "_empleado_por_documento_cliente")
    def test_employee_discount_rejects_missing_or_wrong_password(
        self,
        employee_lookup,
    ):
        buyer = self._buyer(
            password_valid=False,
            role_name="Web Master",
        )
        employee_lookup.return_value = buyer
        cashier = SimpleNamespace(empleado=None)
        client = SimpleNamespace(numerodocumento="12345678")

        with self.assertRaisesRegex(ValueError, "requiere la contrasena"):
            GenerarVentaView._validar_compra_empleado(
                cajero_user=cashier,
                cliente=client,
                empleado_password="",
            )

        with self.assertRaisesRegex(ValueError, "no es correcta"):
            GenerarVentaView._validar_compra_empleado(
                cajero_user=cashier,
                cliente=client,
                empleado_password="clave-incorrecta",
            )

    @patch.object(GenerarVentaView, "_empleado_por_documento_cliente")
    def test_employee_cannot_authorize_own_discount(self, employee_lookup):
        buyer = self._buyer()
        employee_lookup.return_value = buyer

        with self.assertRaisesRegex(ValueError, "no puede autofacturarse"):
            GenerarVentaView._validar_compra_empleado(
                cajero_user=SimpleNamespace(empleado=buyer),
                cliente=SimpleNamespace(numerodocumento="12345678"),
                empleado_password="clave-correcta",
            )

        buyer.usuarioid.check_password.assert_not_called()

    @patch.object(
        GenerarVentaView,
        "_empleado_por_documento_cliente",
        return_value=None,
    )
    def test_regular_client_does_not_need_employee_password(self, _employee_lookup):
        authorized = GenerarVentaView._validar_compra_empleado(
            cajero_user=SimpleNamespace(empleado=None),
            cliente=SimpleNamespace(numerodocumento="99887766"),
            empleado_password="",
        )

        self.assertIsNone(authorized)

    @patch("mainApp.views.Empleado.objects.select_related")
    def test_ambiguous_employee_document_is_never_authorized(self, select_related):
        first = self._buyer(pk=81, document="12345678")
        second = self._buyer(pk=82, document="12.345.678")
        select_related.return_value.all.return_value = [first, second]

        with self.assertRaisesRegex(ValueError, "varios empleados"):
            GenerarVentaView._validar_compra_empleado(
                cajero_user=SimpleNamespace(empleado=None),
                cliente=SimpleNamespace(numerodocumento="12345678"),
                empleado_password="clave-correcta",
            )

        first.usuarioid.check_password.assert_not_called()
        second.usuarioid.check_password.assert_not_called()

    def test_web_master_employee_always_has_zero_total_after_authorization(self):
        buyer = self._buyer(role_name="  WEB_MASTER  ")

        discount, total, free_sale = GenerarVentaView._employee_sale_pricing(
            buyer,
            Decimal("12345"),
        )

        self.assertEqual(discount, Decimal("12345"))
        self.assertEqual(total, Decimal("0"))
        self.assertTrue(free_sale)

    def test_regular_employee_keeps_ten_percent_discount(self):
        buyer = self._buyer(role_name="Vendedor")

        discount, total, free_sale = GenerarVentaView._employee_sale_pricing(
            buyer,
            Decimal("12345"),
        )

        self.assertEqual(discount, Decimal("1235"))
        self.assertEqual(total, Decimal("11110"))
        self.assertFalse(free_sale)

    def test_web_master_zero_total_is_exposed_to_sale_ui_and_receipt(self):
        base_dir = settings.BASE_DIR / "mainApp"
        script = (
            base_dir / "static" / "javascript" / "generar_venta.js"
        ).read_text(encoding="utf-8")
        view_source = (base_dir / "views.py").read_text(encoding="utf-8")

        self.assertIn("employee_is_web_master", view_source)
        self.assertIn('"web_master_free_sale"', view_source)
        self.assertIn('"sale_total"', view_source)
        self.assertIn("BENEFICIO WEB MASTER:", view_source)
        self.assertIn("employeeIsWebMaster", script)
        self.assertIn("beneficio Web Master del 100%", script)
        self.assertIn("r.web_master_free_sale ? 0", script)
        self.assertIn("shouldKickCashDrawer = totalNum > 0", script)

    def test_web_master_receipt_shows_full_benefit_and_zero_total(self):
        receipt = GenerarVentaView._build_receipt_text(
            {
                "cajero_nombre": "Cajero",
                "descuento_empleado": Decimal("12345"),
                "empleado_comprador": "Ana Ruiz",
                "beneficio_web_master": True,
            },
            [{
                "producto": "Producto",
                "cantidad": 1,
                "precio_unitario": Decimal("12345"),
                "subtotal": Decimal("12345"),
            }],
            Decimal("0"),
            [],
        )

        self.assertIn("BENEFICIO WEB MASTER:", receipt)
        self.assertIn("Empleado: Ana Ruiz", receipt)
        self.assertIn("TOTAL:", receipt)
        self.assertIn("$0", receipt)

    def test_zero_total_never_requires_or_keeps_payments(self):
        payments = GenerarVentaView._normalize_payments(
            [{"medio_pago": "efectivo", "monto": "1000"}],
            Decimal("0"),
            "efectivo",
        )

        self.assertEqual(payments, [])


class RefundPaymentMethodTests(SimpleTestCase):
    @patch("mainApp.views.connection.introspection.table_names", return_value=[])
    def test_sale_page_can_detect_pending_refund_migration(self, _table_names):
        self.assertFalse(_reintegro_ledger_ready())

    def test_cash_refund_is_subtracted_from_cash_not_original_nequi_payment(self):
        expected = {
            "nequi": Decimal("1000.00"),
            "efectivo": Decimal("0.00"),
        }

        result = _aplicar_reintegros_a_esperados(
            expected,
            {"efectivo": Decimal("500.00")},
        )

        self.assertEqual(result["nequi"], Decimal("1000.00"))
        self.assertEqual(result["efectivo"], Decimal("-500.00"))
        self.assertEqual(sum(result.values()), Decimal("500.00"))

    def test_turno_payment_method_accepts_negative_expected_balance(self):
        medio = SimpleNamespace(
            esperado=Decimal("0.00"),
            contado=Decimal("0.00"),
            diferencia=Decimal("0.00"),
            save=MagicMock(),
        )
        locked = MagicMock()
        locked.filter.return_value.first.return_value = medio

        with patch.object(
            TurnoCajaMedio.objects,
            "select_for_update",
            return_value=locked,
        ):
            CambioDevolucion._upsert_turno_medio_delta(
                SimpleNamespace(pk=1288),
                "efectivo",
                Decimal("-1600.00"),
            )

        self.assertEqual(medio.esperado, Decimal("-1600.00"))
        self.assertEqual(medio.diferencia, Decimal("1600.00"))
        medio.save.assert_called_once_with(
            update_fields=["esperado", "diferencia"]
        )

    def test_turno_payment_method_with_no_count_does_not_retry_save(self):
        medio = SimpleNamespace(
            esperado=Decimal("0.00"),
            contado=None,
            diferencia=Decimal("0.00"),
            save=MagicMock(),
        )
        locked = MagicMock()
        locked.filter.return_value.first.return_value = medio

        with patch.object(
            TurnoCajaMedio.objects,
            "select_for_update",
            return_value=locked,
        ):
            CambioDevolucion._upsert_turno_medio_delta(
                SimpleNamespace(pk=1288),
                "efectivo",
                Decimal("-1600.00"),
            )

        self.assertEqual(medio.esperado, Decimal("-1600.00"))
        medio.save.assert_called_once_with(update_fields=["esperado"])

    def test_turno_payment_method_propagates_database_error_without_retry(self):
        medio = SimpleNamespace(
            esperado=Decimal("0.00"),
            contado=Decimal("0.00"),
            diferencia=Decimal("0.00"),
            save=MagicMock(side_effect=IntegrityError("check constraint")),
        )
        locked = MagicMock()
        locked.filter.return_value.first.return_value = medio

        with patch.object(
            TurnoCajaMedio.objects,
            "select_for_update",
            return_value=locked,
        ):
            with self.assertRaises(IntegrityError):
                CambioDevolucion._upsert_turno_medio_delta(
                    SimpleNamespace(pk=1288),
                    "efectivo",
                    Decimal("-1600.00"),
                )

        self.assertEqual(medio.save.call_count, 1)

    def test_refund_balance_migration_removes_legacy_nonnegative_checks(self):
        migration = (
            settings.BASE_DIR
            / "mainApp"
            / "migrations"
            / "0022_allow_negative_turno_medio_balances.py"
        ).read_text(encoding="utf-8")

        self.assertIn("turno_caja_medios_esperado_check", migration)
        self.assertIn("turno_caja_medios_contado_check", migration)
        self.assertIn("DROP CONSTRAINT IF EXISTS", migration)
        self.assertIn("reverse_sql=migrations.RunSQL.noop", migration)

    def test_refund_distribution_requires_exact_total_and_valid_method(self):
        with self.assertRaisesMessage(ValueError, "igual al total"):
            CambioDevolucion._normalizar_reintegro_map(
                {"efectivo": Decimal("499.00")},
                Decimal("500.00"),
            )

        with self.assertRaisesMessage(ValueError, "no válido"):
            CambioDevolucion._normalizar_reintegro_map(
                {"cripto": Decimal("500.00")},
                Decimal("500.00"),
            )

    @patch("mainApp.models.TurnoCaja.objects.filter")
    @patch("mainApp.models.ReintegroVenta.objects.create")
    @patch("mainApp.models.CambioDevolucion.objects.create")
    @patch("mainApp.models.DetalleVenta.objects.filter")
    @patch.object(CambioDevolucion, "_upsert_inventario_delta")
    @patch.object(CambioDevolucion, "_upsert_turno_medio_delta")
    @patch.object(CambioDevolucion, "_turno_abierto_para_venta_locked")
    @patch.object(
        CambioDevolucion,
        "calcular_total_devolucion",
        return_value=Decimal("500.00"),
    )
    def test_paid_refund_creates_cash_outflow_ledger(
        self,
        _calculate,
        active_shift,
        update_shift_method,
        inventory_delta,
        detail_filter,
        create_change,
        create_refund,
        shift_filter,
    ):
        shift = SimpleNamespace(pk=77)
        active_shift.return_value = shift
        sale = MagicMock(
            total=Decimal("1000.00"),
            mediopago="nequi",
            sucursalid_id=5,
            puntopagoid_id=9,
        )
        detail = SimpleNamespace(pk=31, productoid_id=12)
        actor = SimpleNamespace(pk=1)

        CambioDevolucion.registrar_devolucion(
            sale,
            [{"detalle": detail, "cantidad": 1}],
            reintegro_map={"efectivo": Decimal("500.00")},
            registrado_por=actor,
        )

        create_refund.assert_called_once_with(
            venta=sale,
            turno=shift,
            medio_pago="efectivo",
            monto=Decimal("500.00"),
            registrado_por=actor,
        )
        update_shift_method.assert_called_once_with(
            shift,
            "efectivo",
            Decimal("-500.00"),
        )
        sale.save.assert_called_once_with(update_fields=["total"])
        self.assertEqual(sale.total, Decimal("500.00"))
        inventory_delta.assert_called_once_with(5, 12, 1)
        create_change.assert_called_once()
        shift_filter.assert_called()

    def test_sale_detail_ui_collects_refund_method_for_every_sale(self):
        base_dir = settings.BASE_DIR / "mainApp"
        template = (base_dir / "templates" / "ver_venta.html").read_text(
            encoding="utf-8"
        )
        script = (
            base_dir / "static" / "javascript" / "ver_venta.js"
        ).read_text(encoding="utf-8")
        close_script = (
            base_dir / "static" / "javascript" / "turno_caja.js"
        ).read_text(encoding="utf-8")

        self.assertIn("¿Por qué medio entregaste el dinero?", template)
        self.assertIn('data-reintegro-target=', template)
        self.assertIn("VENTA_TOTAL_COBRADO", script)
        self.assertIn("const reintegrado", close_script)
        self.assertIn("efectivoEntregado - BASE", close_script)


class FreeSaleReturnTests(SimpleTestCase):
    @patch.object(CambioDevolucion, "_turno_abierto_para_venta_locked")
    @patch.object(CambioDevolucion, "_upsert_inventario_delta")
    @patch("mainApp.models.DetalleVenta.objects.filter")
    @patch("mainApp.models.CambioDevolucion.objects.create")
    @patch.object(
        CambioDevolucion,
        "calcular_total_devolucion",
        return_value=Decimal("0.00"),
    )
    def test_free_return_restores_inventory_without_financial_movement(
        self,
        _calculate,
        create_change,
        detail_filter,
        inventory_delta,
        open_shift,
    ):
        sale = MagicMock(
            total=Decimal("0.00"),
            sucursalid_id=5,
            mediopago="sin_pago",
        )
        detail = SimpleNamespace(pk=31, productoid_id=77)

        CambioDevolucion.registrar_devolucion(
            sale,
            [{"detalle": detail, "cantidad": 2}],
        )

        inventory_delta.assert_called_once_with(5, 77, 2)
        detail_filter.assert_called_once_with(pk=31)
        detail_filter.return_value.update.assert_called_once()
        create_change.assert_called_once()
        self.assertEqual(sale.total, Decimal("0.00"))
        sale.save.assert_called_once_with(update_fields=["total"])
        open_shift.assert_not_called()
