from decimal import Decimal
from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone as dt_timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, IntegrityError
from django.db.models.deletion import PROTECT
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve, reverse

from .forms import GenerarVentaForm
from .models import CambioDevolucion, ReintegroVenta, TurnoCajaMedio
from .permissions import WEB_MASTER_ONLY_URL_NAMES, route_permission_for_url_name
from .services.employee_client import (
    EmployeeClientSyncError,
    _matching_clients,
    sync_employee_client,
)
from .services.feature_flags import (
    ActiveCashTurnError,
    FEATURE_REGISTRY,
    NEQUI_API_FEATURE,
    TURN_REQUIRED_FEATURE,
    FeatureFlagError,
    disabled_feature_for_url,
    feature_definition,
    is_feature_enabled,
    set_feature_enabled,
)
from .services.product_price_sync import (
    PriceMapping,
    ProductPriceSyncError,
    SourceProduct,
    _validate_source_rows,
    fetch_source_products,
    load_price_mappings,
    normalize_product_name,
    sync_product_prices,
)
from .services.special_discount import (
    SpecialDiscountError,
    consume_one_time_code,
    generate_one_time_code,
    is_special_client,
    lock_one_time_code,
    preview_one_time_code,
)
from .views import (
    ClavesDescuentoMerk2888View,
    ConfiguracionFuncionalidadesView,
    GenerarVentaView,
    NequiNotificacionesDisponiblesView,
    NequiNotificationWebhookView,
    ProductoAutocomplete,
    TurnoCajaIniciarApi,
    TurnoCajaRecuperarOIniciarView,
    VentaDetailView,
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

    @override_settings(MACRODROID_NEQUI_TOKEN="test-nequi-token")
    def test_disabled_api_rejects_valid_webhook_without_writing(self):
        request = self.factory.post(
            reverse("macrodroid_nequi_webhook"),
            data=json.dumps({
                "event_id": "event-disabled-1",
                "not_title": "Pago recibido",
                "not_text": "Ana Ruiz te envio $10.000",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-nequi-token",
        )

        with (
            patch(
                "mainApp.views.is_feature_enabled",
                return_value=False,
            ) as feature_check,
            patch(
                "mainApp.views.NotificacionNequi.objects.get_or_create",
            ) as create_notification,
        ):
            response = NequiNotificationWebhookView().post(request)

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(payload["success"])
        self.assertEqual(
            payload["feature_disabled"],
            NEQUI_API_FEATURE,
        )
        self.assertEqual(response["Cache-Control"], "no-store, max-age=0")
        feature_check.assert_called_once_with(
            NEQUI_API_FEATURE,
            fresh=True,
        )
        create_notification.assert_not_called()

    @override_settings(MACRODROID_NEQUI_TOKEN="test-nequi-token")
    def test_invalid_token_does_not_reveal_disabled_api_state(self):
        request = self.factory.post(
            reverse("macrodroid_nequi_webhook"),
            data=json.dumps({"not_title": "Pago recibido"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer invalid-token",
        )

        with patch("mainApp.views.is_feature_enabled") as feature_check:
            response = NequiNotificationWebhookView().post(request)

        self.assertEqual(response.status_code, 403)
        self.assertNotIn("feature_disabled", json.loads(response.content))
        feature_check.assert_not_called()

    def test_available_payments_endpoint_is_blocked_without_querying(self):
        request = self.factory.get(
            reverse("nequi_notificaciones_disponibles"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = SimpleNamespace(is_authenticated=True)

        with (
            patch(
                "mainApp.views.is_feature_enabled",
                return_value=False,
            ),
            patch(
                "mainApp.views.NotificacionNequi.objects.filter",
            ) as notifications,
        ):
            response = NequiNotificacionesDisponiblesView().get(request)

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            json.loads(response.content)["feature_disabled"],
            NEQUI_API_FEATURE,
        )
        notifications.assert_not_called()

    @override_settings(MACRODROID_NEQUI_TOKEN="test-nequi-token")
    def test_enabled_api_preserves_webhook_creation_flow(self):
        request = self.factory.post(
            reverse("macrodroid_nequi_webhook"),
            data=json.dumps({
                "event_id": "event-enabled-1",
                "not_title": "Pago recibido Nequi",
                "not_text": "Ana Ruiz te envio $10.000",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-nequi-token",
        )
        notification = SimpleNamespace(
            notificacionid=81,
            titulo="Pago recibido Nequi",
            texto="Ana Ruiz te envio $10.000",
            app="Nequi",
            paquete="com.nequi.MobileApp",
            monto=Decimal("10000.00"),
            remitente="Ana Ruiz",
            referencia="",
            recibido_en=datetime.now(dt_timezone.utc),
            venta_id=None,
        )

        with (
            patch(
                "mainApp.views.is_feature_enabled",
                return_value=True,
            ),
            patch(
                "mainApp.views.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "mainApp.views.locked_feature_enabled",
                return_value=True,
            ) as locked_feature,
            patch(
                "mainApp.views.NotificacionNequi.objects.get_or_create",
                return_value=(notification, True),
            ) as create_notification,
        ):
            response = NequiNotificationWebhookView().post(request)

        self.assertEqual(response.status_code, 201)
        self.assertTrue(json.loads(response.content)["created"])
        locked_feature.assert_called_once_with(NEQUI_API_FEATURE)
        create_notification.assert_called_once()


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
        self.assertIn(
            "r.web_master_free_sale || r.merk2888_free_sale",
            script,
        )
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
        self.assertEqual(
            CambioDevolucion._normalizar_reintegro_map({}, Decimal("500.00")),
            {"efectivo": Decimal("500.00")},
        )
        self.assertEqual(
            CambioDevolucion._normalizar_reintegro_map({}, Decimal("0.00")),
            {},
        )

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

    def test_empty_refund_form_defaults_to_cash_without_overwriting_choice(self):
        view = VentaDetailView()
        empty_formset = SimpleNamespace(
            is_valid=lambda: True,
            cleaned_data=[
                {"medio_pago": medio, "monto": Decimal("0.00")}
                for medio in ("efectivo", "nequi", "daviplata", "tarjeta", "banco_caja_social")
            ],
        )

        ok, error, refund_map = view._validar_reintegro_mixto(
            SimpleNamespace(),
            empty_formset,
            Decimal("500.00"),
        )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(refund_map, {"efectivo": Decimal("500.00")})

        nequi_formset = SimpleNamespace(
            is_valid=lambda: True,
            cleaned_data=[
                {"medio_pago": "efectivo", "monto": Decimal("0.00")},
                {"medio_pago": "nequi", "monto": Decimal("500.00")},
            ],
        )
        ok, error, refund_map = view._validar_reintegro_mixto(
            SimpleNamespace(),
            nequi_formset,
            Decimal("500.00"),
        )

        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(refund_map, {"nequi": Decimal("500.00")})

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
        self.assertIn("Por defecto, todo el dinero se asigna a Efectivo", template)
        self.assertIn('data-reintegro-target=', template)
        self.assertIn("VENTA_TOTAL_COBRADO", script)
        self.assertIn("applyDefaultCashRefund", script)
        self.assertIn('row.medio === "efectivo" ? to2(totalDev)', script)
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


class ProductPriceMappingTests(SimpleTestCase):
    def test_repository_mapping_preserves_one_to_many_relations(self):
        mapping_path = (
            settings.BASE_DIR
            / "mainApp"
            / "data"
            / "price_sync_plaza_map.json"
        )
        payload = json.loads(mapping_path.read_text(encoding="utf-8"))
        mappings = load_price_mappings(mapping_path)

        self.assertEqual(len(payload["mappings"]), 76)
        self.assertEqual(
            len([item for item in payload["mappings"] if not item["active"]]),
            6,
        )
        self.assertEqual(len(mappings), 70)
        self.assertEqual(len({item.source_id for item in mappings}), 66)
        self.assertEqual(len({item.destination_id for item in mappings}), 70)
        self.assertEqual(
            sorted(
                item.destination_id
                for item in mappings
                if item.source_id == 1768
            ),
            [25062319, 25062320, 25062321],
        )
        self.assertEqual(
            next(item for item in mappings if item.source_id == 383).expected_source_name,
            "Piña x gr",
        )
        self.assertEqual(
            next(item for item in mappings if item.source_id == 359).expected_source_name,
            "Champiñones Bandeja",
        )
        self.assertEqual(
            next(
                item
                for item in mappings
                if item.destination_id == 25062095
            ).expected_destination_name,
            "FR PAQUETE GUASCA",
        )
        self.assertEqual(
            next(
                item
                for item in mappings
                if item.destination_id == 25062126
            ).expected_destination_name,
            "FR TOMILLO Y LAUREL",
        )
        maximum_factor = Decimal(settings.PRICE_SYNC_MAX_PRICE_FACTOR)
        for item in payload["mappings"]:
            if not item["active"]:
                continue
            source = Decimal(item["source_snapshot_price"])
            destination = Decimal(item["destination_snapshot_price"])
            factor = max(source / destination, destination / source)
            self.assertLessEqual(
                factor,
                maximum_factor,
                msg=f"Mapeo extremo activo: {item['source_id']} -> {item['destination_id']}",
            )

    def test_mapping_rejects_destination_linked_to_two_sources(self):
        payload = {
            "version": 1,
            "mappings": [
                {
                    "source_id": 1,
                    "expected_source_name": "Origen A",
                    "destination_id": 10,
                    "expected_destination_name": "Destino",
                },
                {
                    "source_id": 2,
                    "expected_source_name": "Origen B",
                    "destination_id": 10,
                    "expected_destination_name": "Destino",
                },
            ],
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            path.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaisesMessage(
                ProductPriceSyncError,
                "dos productos de origen",
            ):
                load_price_mappings(path)

    def test_product_name_normalization_is_strict_but_accent_insensitive(self):
        self.assertEqual(
            normalize_product_name("  PIÑA   X-KG "),
            normalize_product_name("piña x kg"),
        )
        self.assertNotEqual(
            normalize_product_name("Piña x kg"),
            normalize_product_name("Papaya x kg"),
        )


class ProductPriceSourceValidationTests(SimpleTestCase):
    mapping = PriceMapping(
        source_id=7,
        expected_source_name="Piña x kg",
        destination_id=70,
        expected_destination_name="FR PIÑA XKG",
    )

    def test_source_rows_require_id_and_expected_name(self):
        products = _validate_source_rows(
            [(7, "PIÑA X KG", Decimal("3.845"))],
            (self.mapping,),
        )
        self.assertEqual(products[7].price, Decimal("3.85"))

        with self.assertRaisesMessage(
            ProductPriceSyncError,
            "no corresponden al catálogo",
        ):
            _validate_source_rows(
                [(7, "Aceite x 500 ml", Decimal("3000"))],
                (self.mapping,),
            )

        with self.assertRaisesMessage(
            ProductPriceSyncError,
            "menor a un centavo",
        ):
            _validate_source_rows(
                [(7, "Piña x kg", Decimal("0.004"))],
                (self.mapping,),
            )

    @override_settings(
        PRICE_SYNC_SOURCE_HOST="source.example",
        PRICE_SYNC_SOURCE_PORT="5432",
        PRICE_SYNC_SOURCE_NAME="catalog",
        PRICE_SYNC_SOURCE_USER="reader",
        PRICE_SYNC_SOURCE_PASSWORD="secret",
        PRICE_SYNC_SOURCE_SSLMODE="require",
        PRICE_SYNC_CONNECT_TIMEOUT="3",
        PRICE_SYNC_STATEMENT_TIMEOUT_MS="1000",
    )
    def test_external_connection_is_read_only_and_always_closed(self):
        class FakeCursor:
            def __init__(self):
                self.executed = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def execute(self, sql, params=None):
                self.executed.append((sql, params))

            def fetchone(self):
                return ("on",)

            def fetchall(self):
                return [(7, "Piña x kg", Decimal("3.80"))]

        class FakeConnection:
            def __init__(self):
                self.fake_cursor = FakeCursor()
                self.set_session = MagicMock()
                self.rollback = MagicMock()
                self.close = MagicMock()

            def cursor(self):
                return self.fake_cursor

        connection = FakeConnection()
        connect = MagicMock(return_value=connection)

        products = fetch_source_products((self.mapping,), connect=connect)

        self.assertEqual(products[7].price, Decimal("3.80"))
        connection.set_session.assert_called_once_with(
            readonly=True,
            autocommit=False,
        )
        self.assertIn(
            "FROM public.productos",
            connection.fake_cursor.executed[-1][0],
        )
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()


class ProductPriceSyncServiceTests(SimpleTestCase):
    mapping = PriceMapping(
        source_id=7,
        expected_source_name="Piña x kg",
        destination_id=70,
        expected_destination_name="FR PIÑA XKG",
    )

    def _source(self, price):
        return {
            7: SourceProduct(
                product_id=7,
                name="Piña x kg",
                price=Decimal(price),
            )
        }

    @patch("mainApp.services.product_price_sync.Producto.objects.bulk_update")
    @patch("mainApp.services.product_price_sync._load_destination_products")
    @patch("mainApp.services.product_price_sync.fetch_source_products")
    def test_dry_run_never_writes(
        self,
        fetch_source,
        load_destination,
        bulk_update,
    ):
        fetch_source.return_value = self._source("12.00")
        load_destination.return_value = {
            70: SimpleNamespace(
                nombre="FR PIÑA XKG",
                precio=Decimal("10.00"),
                precio_anterior=None,
            )
        }

        report = sync_product_prices(
            apply=False,
            mappings=(self.mapping,),
        )

        self.assertFalse(report.applied)
        self.assertEqual(len(report.changes), 1)
        bulk_update.assert_not_called()

    @override_settings(PRICE_SYNC_MAX_PRICE_FACTOR="5")
    @patch(
        "mainApp.services.product_price_sync._exclusive_sync_lock",
        return_value=nullcontext(),
    )
    @patch(
        "mainApp.services.product_price_sync.transaction.atomic",
        return_value=nullcontext(),
    )
    @patch("mainApp.services.product_price_sync.Producto.objects.bulk_update")
    @patch("mainApp.services.product_price_sync._load_destination_products")
    @patch("mainApp.services.product_price_sync.fetch_source_products")
    def test_apply_preserves_previous_price(
        self,
        fetch_source,
        load_destination,
        bulk_update,
        _atomic,
        _sync_lock,
    ):
        product = SimpleNamespace(
            nombre="FR PIÑA XKG",
            precio=Decimal("10.00"),
            precio_anterior=None,
        )
        fetch_source.return_value = self._source("12.00")
        load_destination.return_value = {70: product}

        report = sync_product_prices(
            apply=True,
            mappings=(self.mapping,),
        )

        self.assertTrue(report.applied)
        self.assertEqual(product.precio_anterior, Decimal("10.00"))
        self.assertEqual(product.precio, Decimal("12.00"))
        bulk_update.assert_called_once_with(
            [product],
            ["precio_anterior", "precio"],
            batch_size=100,
        )

    @override_settings(PRICE_SYNC_MAX_PRICE_FACTOR="5")
    @patch(
        "mainApp.services.product_price_sync._exclusive_sync_lock",
        return_value=nullcontext(),
    )
    @patch(
        "mainApp.services.product_price_sync.transaction.atomic",
        return_value=nullcontext(),
    )
    @patch("mainApp.services.product_price_sync.Producto.objects.bulk_update")
    @patch("mainApp.services.product_price_sync._load_destination_products")
    @patch("mainApp.services.product_price_sync.fetch_source_products")
    def test_apply_blocks_extreme_unit_mismatch(
        self,
        fetch_source,
        load_destination,
        bulk_update,
        _atomic,
        _sync_lock,
    ):
        fetch_source.return_value = self._source("3.00")
        load_destination.return_value = {
            70: SimpleNamespace(
                nombre="FR PIÑA XKG",
                precio=Decimal("3000.00"),
                precio_anterior=None,
            )
        }

        with self.assertRaisesMessage(
            ProductPriceSyncError,
            "variación superior",
        ):
            sync_product_prices(
                apply=True,
                mappings=(self.mapping,),
            )
        bulk_update.assert_not_called()

    @patch("mainApp.services.product_price_sync.fetch_source_products")
    def test_empty_explicit_mapping_never_falls_back_to_full_catalog(
        self,
        fetch_source,
    ):
        with self.assertRaisesMessage(
            ProductPriceSyncError,
            "No hay productos configurados",
        ):
            sync_product_prices(apply=False, mappings=())
        fetch_source.assert_not_called()


class ProductPriceScheduleTests(SimpleTestCase):
    def test_schedule_uses_colombia_weekdays(self):
        from mainApp.management.commands.actualizar_precios_plaza import (
            should_run_today,
        )

        self.assertTrue(should_run_today(date(2026, 7, 28)))  # martes
        self.assertTrue(should_run_today(date(2026, 7, 31)))  # viernes
        self.assertTrue(should_run_today(date(2026, 8, 2)))  # domingo
        self.assertFalse(should_run_today(date(2026, 8, 1)))  # sábado
        self.assertFalse(should_run_today(date(2026, 7, 29)))  # miércoles

    @patch(
        "mainApp.management.commands.actualizar_precios_plaza.sync_product_prices"
    )
    def test_command_defaults_to_dry_run(self, sync_prices):
        sync_prices.return_value = SimpleNamespace(
            applied=False,
            mapping_count=70,
            source_product_count=66,
            destination_product_count=70,
            changes=(),
            unchanged_count=70,
            suspicious_changes=(),
        )
        output = StringIO()

        call_command(
            "actualizar_precios_plaza",
            "--force",
            stdout=output,
        )

        sync_prices.assert_called_once_with(apply=False)
        self.assertIn("SIMULACIÓN", output.getvalue())
        self.assertIn("No se modificó ningún precio", output.getvalue())

    @patch(
        "mainApp.management.commands.actualizar_precios_plaza.sync_product_prices",
        side_effect=ProductPriceSyncError("catálogo incorrecto"),
    )
    def test_command_converts_safe_validation_error_to_command_error(
        self,
        _sync_prices,
    ):
        with self.assertRaisesMessage(CommandError, "catálogo incorrecto"):
            call_command(
                "actualizar_precios_plaza",
                "--apply",
                "--force",
            )


class SpecialMerk2888DiscountSecurityTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.fixed_now = datetime(
            2026,
            7,
            29,
            15,
            0,
            tzinfo=dt_timezone.utc,
        )

    def _authorization(self, **overrides):
        values = {
            "pk": 41,
            "usada_en": None,
            "venta_id": None,
            "revocada_en": None,
            "bloqueada_en": None,
            "intentos_fallidos": 0,
            "expira_en": self.fixed_now + timedelta(minutes=15),
            "selector": "selector-seguro",
            "secreto_hash": "hash-seguro",
            "save": MagicMock(),
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_key_route_is_mapped_and_strictly_web_master_only(self):
        url = reverse("claves_descuento_merk2888")

        self.assertEqual(resolve(url).url_name, "claves_descuento_merk2888")
        self.assertEqual(
            route_permission_for_url_name("claves_descuento_merk2888"),
            "descuentos_especiales_generar",
        )
        self.assertIn(
            "claves_descuento_merk2888",
            WEB_MASTER_ONLY_URL_NAMES,
        )

        denied_request = self.factory.get(url)
        denied_request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
        )
        with patch("mainApp.views.is_web_master_role", return_value=False):
            denied = ClavesDescuentoMerk2888View.as_view()(denied_request)
        self.assertEqual(denied.status_code, 403)

        allowed_request = self.factory.get(url)
        allowed_request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
        )
        with (
            patch("mainApp.views.is_web_master_role", return_value=True),
            patch.object(
                ClavesDescuentoMerk2888View,
                "get",
                return_value=HttpResponse("ok"),
            ),
        ):
            allowed = ClavesDescuentoMerk2888View.as_view()(allowed_request)
        self.assertEqual(allowed.status_code, 200)

    @patch(
        "mainApp.services.special_discount.get_special_client_profile",
        return_value=None,
    )
    def test_client_named_merk2888_is_not_special_without_canonical_profile(
        self,
        get_profile,
    ):
        impostor = SimpleNamespace(
            pk=987,
            nombre="merk2888",
            apellido="",
            numerodocumento="DOCUMENTO-CUALQUIERA",
        )

        self.assertFalse(is_special_client(impostor))
        get_profile.assert_called_once_with(cliente=impostor)

    def test_generated_code_is_hashed_and_plaintext_is_never_persisted(self):
        manager = MagicMock()
        manager.select_for_update.return_value.filter.return_value.update.return_value = 1
        manager.filter.return_value.exists.return_value = False
        stored_authorization = SimpleNamespace(pk=88)
        manager.create.return_value = stored_authorization
        fake_model = SimpleNamespace(objects=manager)
        profile = SimpleNamespace(pk=5, cliente_id=10)
        web_master = SimpleNamespace(
            pk=3,
            nombreusuario="webmaster",
            is_authenticated=True,
            is_active=True,
        )

        with (
            patch(
                "mainApp.services.special_discount.AutorizacionDescuentoEspecial",
                fake_model,
            ),
            patch(
                "mainApp.services.special_discount.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "mainApp.services.special_discount.get_special_client_profile",
                return_value=profile,
            ),
            patch(
                "mainApp.services.special_discount.is_web_master_role",
                return_value=True,
            ),
            patch(
                "mainApp.services.special_discount.timezone.now",
                return_value=self.fixed_now,
            ),
            patch(
                "mainApp.services.special_discount.secrets.randbelow",
                return_value=1234567,
            ),
            patch(
                "mainApp.services.special_discount.secrets.token_hex",
                side_effect=lambda size: (
                    "a" * 64 if size == 32 else "b" * 8
                ),
            ),
            patch(
                "mainApp.services.special_discount.make_password",
                return_value="pbkdf2_sha256$hash-no-reversible",
            ) as make_password,
        ):
            authorization, plain_code = generate_one_time_code(
                web_master,
                "c" * 32,
            )

        self.assertIs(authorization, stored_authorization)
        self.assertEqual(plain_code, "01234567")
        make_password.assert_called_once_with(plain_code)

        create_kwargs = manager.create.call_args.kwargs
        self.assertEqual(
            create_kwargs["secreto_hash"],
            "pbkdf2_sha256$hash-no-reversible",
        )
        self.assertEqual(create_kwargs["solicitud_id"], "c" * 32)
        self.assertNotEqual(create_kwargs["selector"], plain_code)
        self.assertEqual(len(create_kwargs["selector"]), 64)
        self.assertNotIn(plain_code, repr(create_kwargs))

    def test_preview_blocks_code_on_fifth_failed_attempt(self):
        authorization = self._authorization(intentos_fallidos=4)
        manager = MagicMock()
        (
            manager.select_for_update.return_value
            .filter.return_value
            .order_by.return_value
            .first.return_value
        ) = authorization
        fake_model = SimpleNamespace(objects=manager)
        profile = SimpleNamespace(pk=9)
        client = SimpleNamespace(pk=77)
        actor = SimpleNamespace(pk=12, nombreusuario="cajero-prueba")

        with (
            patch(
                "mainApp.services.special_discount.AutorizacionDescuentoEspecial",
                fake_model,
            ),
            patch(
                "mainApp.services.special_discount.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "mainApp.services.special_discount.get_special_client_profile",
                return_value=profile,
            ),
            patch(
                "mainApp.services.special_discount.timezone.now",
                return_value=self.fixed_now,
            ),
            patch(
                "mainApp.services.special_discount.check_password",
                return_value=False,
            ),
        ):
            with self.assertRaises(SpecialDiscountError) as raised:
                preview_one_time_code(
                    client,
                    "11111111",
                    actor=actor,
                    ip_address="192.0.2.15",
                )

            self.assertEqual(raised.exception.code, "blocked")
            self.assertEqual(authorization.intentos_fallidos, 5)
            self.assertEqual(authorization.bloqueada_en, self.fixed_now)
            self.assertEqual(
                authorization.ultimo_intento_fallido_por,
                actor,
            )
            self.assertEqual(
                authorization.ultimo_intento_fallido_ip,
                "192.0.2.15",
            )
            authorization.save.assert_called_once_with(
                update_fields=[
                    "intentos_fallidos",
                    "ultimo_intento_fallido_en",
                    "ultimo_intento_fallido_por",
                    "ultimo_intento_fallido_por_nombre",
                    "ultimo_intento_fallido_ip",
                    "bloqueada_en",
                ],
            )

            with self.assertRaises(SpecialDiscountError) as blocked_again:
                preview_one_time_code(client, "11111111")
            self.assertEqual(blocked_again.exception.code, "blocked")
            self.assertEqual(authorization.save.call_count, 1)

    def test_duplicate_generation_request_never_revokes_active_code(self):
        manager = MagicMock()
        manager.filter.return_value.exists.return_value = True
        fake_model = SimpleNamespace(objects=manager)
        profile = SimpleNamespace(pk=5, cliente_id=10)
        web_master = SimpleNamespace(
            pk=3,
            nombreusuario="webmaster",
            is_authenticated=True,
            is_active=True,
        )

        with (
            patch(
                "mainApp.services.special_discount.AutorizacionDescuentoEspecial",
                fake_model,
            ),
            patch(
                "mainApp.services.special_discount.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "mainApp.services.special_discount.get_special_client_profile",
                return_value=profile,
            ),
            patch(
                "mainApp.services.special_discount.is_web_master_role",
                return_value=True,
            ),
        ):
            with self.assertRaises(SpecialDiscountError) as raised:
                generate_one_time_code(web_master, "d" * 32)

        self.assertEqual(raised.exception.code, "duplicate_request")
        manager.select_for_update.assert_not_called()
        manager.create.assert_not_called()

    def test_new_generation_never_silently_replaces_valid_code(self):
        manager = MagicMock()
        duplicate_query = MagicMock()
        duplicate_query.exists.return_value = False
        active_query = MagicMock()
        active_query.exists.return_value = True
        manager.filter.side_effect = [duplicate_query, active_query]
        fake_model = SimpleNamespace(objects=manager)
        profile = SimpleNamespace(pk=5, cliente_id=10)
        web_master = SimpleNamespace(
            pk=3,
            nombreusuario="webmaster",
            is_authenticated=True,
            is_active=True,
        )

        with (
            patch(
                "mainApp.services.special_discount.AutorizacionDescuentoEspecial",
                fake_model,
            ),
            patch(
                "mainApp.services.special_discount.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "mainApp.services.special_discount.get_special_client_profile",
                return_value=profile,
            ),
            patch(
                "mainApp.services.special_discount.is_web_master_role",
                return_value=True,
            ),
            patch(
                "mainApp.services.special_discount.timezone.now",
                return_value=self.fixed_now,
            ),
        ):
            with self.assertRaises(SpecialDiscountError) as raised:
                generate_one_time_code(web_master, "e" * 32)

        self.assertEqual(raised.exception.code, "active_code_exists")
        manager.select_for_update.assert_not_called()
        manager.create.assert_not_called()

    def test_lock_requires_atomic_block_and_uses_select_for_update(self):
        client = SimpleNamespace(pk=77)
        with patch(
            "mainApp.services.special_discount.transaction.get_connection",
            return_value=SimpleNamespace(in_atomic_block=False),
        ):
            with self.assertRaises(SpecialDiscountError) as raised:
                lock_one_time_code(client, "12345678", 41)
        self.assertEqual(raised.exception.code, "atomic_required")

        authorization = self._authorization()
        manager = MagicMock()
        (
            manager.select_for_update.return_value
            .filter.return_value
            .first.return_value
        ) = authorization
        fake_model = SimpleNamespace(objects=manager)
        profile = SimpleNamespace(pk=9)

        with (
            patch(
                "mainApp.services.special_discount.AutorizacionDescuentoEspecial",
                fake_model,
            ),
            patch(
                "mainApp.services.special_discount.transaction.get_connection",
                return_value=SimpleNamespace(in_atomic_block=True),
            ),
            patch(
                "mainApp.services.special_discount.get_special_client_profile",
                return_value=profile,
            ) as get_profile,
            patch(
                "mainApp.services.special_discount.timezone.now",
                return_value=self.fixed_now,
            ),
            patch(
                "mainApp.services.special_discount.check_password",
                return_value=True,
            ),
        ):
            locked = lock_one_time_code(client, "12345678", 41)

        self.assertIs(locked, authorization)
        get_profile.assert_called_once_with(
            cliente=client,
            for_update=True,
        )
        manager.select_for_update.assert_called_once_with()
        manager.select_for_update.return_value.filter.assert_called_once_with(
            pk=41,
            cliente_especial=profile,
        )

    def test_form_ui_and_backend_keep_special_code_separate(self):
        form = GenerarVentaForm()
        self.assertIn("empleado_password", form.fields)
        self.assertIn("codigo_descuento_merk2888", form.fields)
        self.assertIsNot(
            form.fields["empleado_password"],
            form.fields["codigo_descuento_merk2888"],
        )

        base_dir = settings.BASE_DIR / "mainApp"
        page = (base_dir / "templates" / "generar_venta.html").read_text(
            encoding="utf-8",
        )
        modal = (base_dir / "templates" / "modal_venta.html").read_text(
            encoding="utf-8",
        )
        script = (
            base_dir / "static" / "javascript" / "generar_venta.js"
        ).read_text(encoding="utf-8")
        view_source = (base_dir / "views.py").read_text(encoding="utf-8")

        self.assertIn('name="codigo_descuento_merk2888"', page)
        self.assertIn('id="merk2888-password-input"', modal)
        self.assertIn('autocomplete="one-time-code"', modal)
        self.assertIn("$hidMerk2888Password", script)
        self.assertIn('data.get("codigo_descuento_merk2888")', view_source)
        self.assertIn("preview_one_time_code(", view_source)
        self.assertIn("lock_one_time_code(", view_source)
        self.assertIn("consume_one_time_code(", view_source)

        receipt = GenerarVentaView._build_receipt_text(
            {
                "cajero_nombre": "Cajero",
                "descuento_empleado": Decimal("5000"),
                "beneficio_merk2888": True,
            },
            [{
                "producto": "Producto",
                "cantidad": 1,
                "precio_unitario": Decimal("5000"),
                "subtotal": Decimal("5000"),
            }],
            Decimal("0"),
            [],
        )
        self.assertIn("BENEFICIO MERK2888:", receipt)
        self.assertNotIn("BENEFICIO WEB MASTER:", receipt)

    def test_employee_discount_regressions_remain_unchanged(self):
        regular_employee = SimpleNamespace(
            usuarioid=SimpleNamespace(
                rolid=SimpleNamespace(nombre="Vendedor"),
            ),
        )
        web_master_employee = SimpleNamespace(
            usuarioid=SimpleNamespace(
                rolid=SimpleNamespace(nombre="Web Master"),
            ),
        )

        regular_discount, regular_total, regular_free = (
            GenerarVentaView._employee_sale_pricing(
                regular_employee,
                Decimal("10000"),
            )
        )
        web_discount, web_total, web_free = (
            GenerarVentaView._employee_sale_pricing(
                web_master_employee,
                Decimal("10000"),
            )
        )

        self.assertEqual(regular_discount, Decimal("1000"))
        self.assertEqual(regular_total, Decimal("9000"))
        self.assertFalse(regular_free)
        self.assertEqual(web_discount, Decimal("10000"))
        self.assertEqual(web_total, Decimal("0"))
        self.assertTrue(web_free)

    def test_migration_0023_is_scoped_and_seeds_canonical_client(self):
        migration_path = (
            settings.BASE_DIR
            / "mainApp"
            / "migrations"
            / "0023_special_merk2888_discount.py"
        )
        source = migration_path.read_text(encoding="utf-8")

        self.assertIn(
            '("mainApp", "0022_allow_negative_turno_medio_balances")',
            source,
        )
        self.assertEqual(source.count("migrations.CreateModel("), 2)
        self.assertEqual(source.count("migrations.AddIndex("), 1)
        self.assertEqual(source.count("migrations.AddConstraint("), 5)
        self.assertEqual(source.count("migrations.RunPython("), 1)
        self.assertNotIn("migrations.AlterField(", source)
        self.assertNotIn("migrations.RemoveField(", source)
        self.assertNotIn("migrations.DeleteModel(", source)
        self.assertNotIn("migrations.RunSQL(", source)

        self.assertIn('SPECIAL_CLIENT_KEY = "merk2888"', source)
        self.assertIn('SPECIAL_CLIENT_DOCUMENT = "MERK2888"', source)
        self.assertIn(
            ".filter(numerodocumento__iexact=SPECIAL_CLIENT_DOCUMENT)",
            source,
        )
        self.assertIn("if len(matching_clients) > 1:", source)
        self.assertIn("client = Cliente.objects.create(", source)
        self.assertIn(
            "profile, created = ClienteEspecial.objects.get_or_create(",
            source,
        )
        self.assertIn('"cliente_id": client.pk', source)
        self.assertIn('"activo": True', source)
        self.assertIn("if not profile.activo:", source)
        self.assertIn(
            "if _normalize_key(role.nombre) == \"web_master\"",
            source,
        )


class SystemFeatureFlagTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_configuration_route_is_mapped_and_strictly_web_master_only(self):
        url = reverse("configuracion_funcionalidades")

        self.assertEqual(
            resolve(url).url_name,
            "configuracion_funcionalidades",
        )
        self.assertEqual(
            route_permission_for_url_name("configuracion_funcionalidades"),
            "configuracion_funcionalidades",
        )
        self.assertIn(
            "configuracion_funcionalidades",
            WEB_MASTER_ONLY_URL_NAMES,
        )

        anonymous_request = self.factory.get(url)
        anonymous_request.user = SimpleNamespace(is_authenticated=False)
        anonymous = ConfiguracionFuncionalidadesView.as_view()(
            anonymous_request
        )
        self.assertEqual(anonymous.status_code, 302)

        denied_request = self.factory.get(url)
        denied_request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
        )
        with patch("mainApp.views.is_web_master_role", return_value=False):
            denied = ConfiguracionFuncionalidadesView.as_view()(
                denied_request
            )
        self.assertEqual(denied.status_code, 403)

        allowed_request = self.factory.get(url)
        allowed_request.user = SimpleNamespace(
            is_authenticated=True,
            is_active=True,
        )
        with (
            patch("mainApp.views.is_web_master_role", return_value=True),
            patch.object(
                ConfiguracionFuncionalidadesView,
                "get",
                return_value=HttpResponse("ok"),
            ),
        ):
            allowed = ConfiguracionFuncionalidadesView.as_view()(
                allowed_request
            )
        self.assertEqual(allowed.status_code, 200)

    def test_registry_defaults_to_safe_turn_required_state_on_database_error(self):
        definition = feature_definition(TURN_REQUIRED_FEATURE)

        self.assertIn(TURN_REQUIRED_FEATURE, FEATURE_REGISTRY)
        self.assertTrue(definition["default_enabled"])
        self.assertTrue(definition["critical"])

        with patch(
            "mainApp.models.ConfiguracionFuncionalidad.objects.filter",
            side_effect=DatabaseError("table unavailable"),
        ):
            enabled = is_feature_enabled(
                TURN_REQUIRED_FEATURE,
                fresh=True,
            )

        self.assertTrue(enabled)

    def test_nequi_api_is_configurable_and_enabled_by_default(self):
        definition = feature_definition(NEQUI_API_FEATURE)
        migration_source = (
            settings.BASE_DIR
            / "mainApp"
            / "migrations"
            / "0025_seed_nequi_api_feature.py"
        ).read_text(encoding="utf-8")

        self.assertIn(NEQUI_API_FEATURE, FEATURE_REGISTRY)
        self.assertTrue(definition["default_enabled"])
        self.assertTrue(definition["critical"])
        self.assertGreaterEqual(len(definition["impacts"]), 3)
        self.assertIn(
            'NEQUI_API_FEATURE = "nequi_api_recepcion"',
            migration_source,
        )
        self.assertIn("objects.get_or_create", migration_source)

        with patch(
            "mainApp.models.ConfiguracionFuncionalidad.objects.filter",
            side_effect=DatabaseError("table unavailable"),
        ):
            enabled = is_feature_enabled(
                NEQUI_API_FEATURE,
                fresh=True,
            )

        self.assertTrue(enabled)

    def test_disabled_nequi_flag_blocks_reception_and_sale_linking_only(self):
        with patch(
            "mainApp.services.feature_flags.is_feature_enabled",
            return_value=False,
        ):
            disabled = disabled_feature_for_url(
                "macrodroid_nequi_webhook",
                fresh=True,
            )
            self.assertEqual(disabled["key"], NEQUI_API_FEATURE)

            for historical_or_sale_route in {
                "nequi_notificaciones",
                "nequi_notificaciones_data",
                "nequi_notificaciones_disponibles",
                "visualizar_ventas",
                "generar_venta",
            }:
                self.assertIsNone(
                    disabled_feature_for_url(
                        historical_or_sale_route,
                        fresh=True,
                    ),
                    historical_or_sale_route,
                )

    def test_sale_modal_hides_only_nequi_linking_when_api_is_off(self):
        from django.template.loader import render_to_string

        disabled_html = render_to_string(
            "modal_venta.html",
            {"system_features": {NEQUI_API_FEATURE: False}},
        )
        enabled_html = render_to_string(
            "modal_venta.html",
            {"system_features": {NEQUI_API_FEATURE: True}},
        )

        self.assertNotIn('id="nequi-payment-panel"', disabled_html)
        self.assertIn('class="pm-check" value="nequi"', disabled_html)
        self.assertIn('id="nequi-payment-panel"', enabled_html)

        sale_template = (
            settings.BASE_DIR / "mainApp" / "templates" / "generar_venta.html"
        ).read_text(encoding="utf-8")
        sale_script = (
            settings.BASE_DIR
            / "mainApp"
            / "static"
            / "javascript"
            / "generar_venta.js"
        ).read_text(encoding="utf-8")
        self.assertIn("window.nequiApiEnabled", sale_template)
        self.assertIn("system_features.nequi_api_recepcion", sale_template)
        self.assertIn("generar_venta.js' %}?v=26", sale_template)
        self.assertIn("let nequiApiEnabled", sale_script)
        self.assertIn("data?.feature_disabled === NEQUI_FEATURE_KEY", sale_script)
        self.assertIn("disableNequiLinking", sale_script)

    def test_disabled_turn_feature_blocks_operational_routes_but_not_history(self):
        blocked_routes = {
            "turno_caja",
            "turno_recuperar_o_iniciar",
            "turno_caja_puntopago_ac",
            "turno_caja_cajero_ac",
            "turno_caja_iniciar",
            "turno_caja_retiro_actual",
            "turno_caja_retiro",
        }
        historical_routes = {
            "turno_caja_iniciar_cierre",
            "turno_caja_cerrar",
            "turnos_caja_dashboard",
            "turnos_caja_admin",
        }

        with patch(
            "mainApp.services.feature_flags.is_feature_enabled",
            return_value=False,
        ):
            for url_name in blocked_routes:
                disabled = disabled_feature_for_url(
                    url_name,
                    fresh=True,
                )
                self.assertIsNotNone(disabled, url_name)
                self.assertEqual(
                    disabled["key"],
                    TURN_REQUIRED_FEATURE,
                )

            for url_name in historical_routes:
                self.assertIsNone(
                    disabled_feature_for_url(url_name, fresh=True),
                    url_name,
                )

    def _feature_mocks(self, *, row, duplicate=None, active_turns=0):
        from .models import (
            CambioConfiguracionFuncionalidad,
            ConfiguracionFuncionalidad,
            TurnoCaja,
        )

        audit_lookup = MagicMock()
        (
            audit_lookup.select_related.return_value
            .first.return_value
        ) = duplicate

        locked_rows = MagicMock()
        locked_rows.filter.return_value.first.return_value = row

        active_rows = MagicMock()
        active_rows.count.return_value = active_turns

        return (
            patch(
                "mainApp.services.feature_flags.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch.object(
                CambioConfiguracionFuncionalidad.objects,
                "filter",
                return_value=audit_lookup,
            ),
            patch.object(
                CambioConfiguracionFuncionalidad.objects,
                "create",
            ),
            patch.object(
                ConfiguracionFuncionalidad.objects,
                "select_for_update",
                return_value=locked_rows,
            ),
            patch.object(
                TurnoCaja.objects,
                "filter",
                return_value=active_rows,
            ),
        )

    def test_set_feature_refuses_disable_while_cash_turns_are_active(self):
        row = SimpleNamespace(
            habilitada=True,
            version=4,
            save=MagicMock(),
        )
        atomic, audit_filter, audit_create, config_lock, turns_filter = (
            self._feature_mocks(row=row, active_turns=2)
        )

        with (
            atomic,
            audit_filter,
            audit_create as create_audit,
            config_lock,
            turns_filter,
        ):
            with self.assertRaises(ActiveCashTurnError) as raised:
                set_feature_enabled(
                    key=TURN_REQUIRED_FEATURE,
                    enabled=False,
                    expected_version=4,
                    actor=SimpleNamespace(nombreusuario="Web Master"),
                    reason="Cierre de caja desactivado por mantenimiento",
                    request_id=str(uuid4()),
                )

        self.assertEqual(raised.exception.count, 2)
        row.save.assert_not_called()
        create_audit.assert_not_called()

    def test_set_feature_duplicate_request_is_idempotent_without_locking_again(self):
        duplicate = SimpleNamespace(
            nuevo=False,
            funcionalidad=SimpleNamespace(version=8),
        )
        row = SimpleNamespace(
            habilitada=True,
            version=4,
            save=MagicMock(),
        )
        atomic, audit_filter, audit_create, config_lock, turns_filter = (
            self._feature_mocks(row=row, duplicate=duplicate)
        )

        with (
            atomic,
            audit_filter,
            audit_create as create_audit,
            config_lock as lock_feature,
            turns_filter,
        ):
            result = set_feature_enabled(
                key=TURN_REQUIRED_FEATURE,
                enabled=False,
                expected_version=4,
                actor=SimpleNamespace(nombreusuario="Web Master"),
                reason="Cierre de caja desactivado por mantenimiento",
                request_id=str(uuid4()),
            )

        self.assertFalse(result.changed)
        self.assertTrue(result.duplicate)
        self.assertFalse(result.enabled)
        self.assertEqual(result.version, 8)
        lock_feature.assert_not_called()
        create_audit.assert_not_called()

    def test_set_feature_rejects_stale_version_without_mutation(self):
        row = SimpleNamespace(
            habilitada=True,
            version=5,
            save=MagicMock(),
        )
        atomic, audit_filter, audit_create, config_lock, turns_filter = (
            self._feature_mocks(row=row)
        )

        with (
            atomic,
            audit_filter,
            audit_create as create_audit,
            config_lock,
            turns_filter,
        ):
            with self.assertRaises(FeatureFlagError) as raised:
                set_feature_enabled(
                    key=TURN_REQUIRED_FEATURE,
                    enabled=False,
                    expected_version=4,
                    actor=SimpleNamespace(nombreusuario="Web Master"),
                    reason="Cierre de caja desactivado por mantenimiento",
                    request_id=str(uuid4()),
                )

        self.assertEqual(raised.exception.code, "stale_version")
        row.save.assert_not_called()
        create_audit.assert_not_called()

    def test_set_feature_success_increments_version_and_writes_audit(self):
        row = SimpleNamespace(
            habilitada=True,
            version=5,
            save=MagicMock(),
        )
        actor = SimpleNamespace(nombreusuario="Web Master")
        request_id = uuid4()
        atomic, audit_filter, audit_create, config_lock, turns_filter = (
            self._feature_mocks(row=row, active_turns=0)
        )

        with (
            atomic,
            audit_filter,
            audit_create as create_audit,
            config_lock,
            turns_filter,
            patch(
                "mainApp.services.feature_flags.clear_feature_cache",
            ) as clear_cache,
        ):
            result = set_feature_enabled(
                key=TURN_REQUIRED_FEATURE,
                enabled=False,
                expected_version=5,
                actor=actor,
                reason="Mantenimiento del control de turnos",
                request_id=str(request_id),
                ip="192.0.2.10",
                user_agent="test-agent",
            )

        self.assertTrue(result.changed)
        self.assertFalse(result.duplicate)
        self.assertFalse(result.enabled)
        self.assertEqual(result.version, 6)
        self.assertFalse(row.habilitada)
        self.assertEqual(row.version, 6)
        row.save.assert_called_once()
        create_audit.assert_called_once_with(
            funcionalidad=row,
            anterior=True,
            nuevo=False,
            motivo="Mantenimiento del control de turnos",
            cambiado_por=actor,
            cambiado_por_nombre="Web Master",
            ip="192.0.2.10",
            user_agent="test-agent",
            solicitud_id=request_id,
        )
        clear_cache.assert_called_once_with(TURN_REQUIRED_FEATURE)

    def test_nequi_api_change_uses_audit_without_checking_cash_turns(self):
        row = SimpleNamespace(
            habilitada=True,
            version=2,
            save=MagicMock(),
        )
        actor = SimpleNamespace(nombreusuario="Web Master")
        atomic, audit_filter, audit_create, config_lock, turns_filter = (
            self._feature_mocks(row=row, active_turns=9)
        )

        with (
            atomic,
            audit_filter,
            audit_create as create_audit,
            config_lock,
            turns_filter as query_active_turns,
            patch(
                "mainApp.services.feature_flags.clear_feature_cache",
            ) as clear_cache,
        ):
            result = set_feature_enabled(
                key=NEQUI_API_FEATURE,
                enabled=False,
                expected_version=2,
                actor=actor,
                reason="Mantenimiento temporal de la integración Nequi",
                request_id=str(uuid4()),
            )

        self.assertTrue(result.changed)
        self.assertFalse(result.enabled)
        query_active_turns.assert_not_called()
        create_audit.assert_called_once()
        clear_cache.assert_called_once_with(NEQUI_API_FEATURE)


class SaleWithoutCashTurnTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _post_request(self, user):
        request = self.factory.post(
            reverse("generar_venta"),
            {
                "sucursal": "999",
                "puntopago": "21",
                "productos": "[]",
                "cantidades": "[]",
                "pagos": "[]",
            },
        )
        request.user = user
        return request

    def test_sale_without_turn_uses_employee_branch_and_accepts_matching_point(self):
        branch = SimpleNamespace(pk=7)
        payment_point = SimpleNamespace(pk=21, sucursalid_id=7)
        user = SimpleNamespace(
            pk=3,
            empleado=SimpleNamespace(sucursalid=branch),
        )
        form = SimpleNamespace(
            is_valid=lambda: True,
            cleaned_data={
                "puntopago": payment_point,
                "productos": [],
                "cantidades": [],
            },
        )
        view = GenerarVentaView()

        with (
            patch(
                "mainApp.views.is_feature_enabled",
                return_value=False,
            ),
            patch.object(
                view,
                "_get_turno_activo",
                return_value=None,
            ) as active_turn,
            patch(
                "mainApp.views.GenerarVentaForm",
                return_value=form,
            ) as form_class,
        ):
            response = view.post(self._post_request(user))

        payload = json.loads(response.content)
        self.assertIn("Carrito", payload["error"])
        self.assertNotIn("punto de pago seleccionado", payload["error"])
        active_turn.assert_not_called()
        posted_data = form_class.call_args.args[0]
        self.assertEqual(posted_data["sucursal"], "7")
        self.assertEqual(posted_data["puntopago"], "21")

    def test_sale_without_turn_rejects_point_from_another_branch(self):
        branch = SimpleNamespace(pk=7)
        payment_point = SimpleNamespace(pk=21, sucursalid_id=8)
        user = SimpleNamespace(
            pk=3,
            empleado=SimpleNamespace(sucursalid=branch),
        )
        form = SimpleNamespace(
            is_valid=lambda: True,
            cleaned_data={
                "puntopago": payment_point,
                "productos": [],
                "cantidades": [],
            },
        )

        with (
            patch(
                "mainApp.views.is_feature_enabled",
                return_value=False,
            ),
            patch(
                "mainApp.views.GenerarVentaForm",
                return_value=form,
            ),
        ):
            response = GenerarVentaView().post(
                self._post_request(user)
            )

        payload = json.loads(response.content)
        self.assertIn(
            "no pertenece a tu sucursal asignada",
            payload["error"],
        )

    def test_final_sale_transaction_rechecks_feature_and_blocks_stale_off_page(self):
        from .models import TurnoCaja, Venta

        state = {"inside_atomic": False}

        class AtomicGuard:
            def __enter__(self):
                state["inside_atomic"] = True
                return self

            def __exit__(self, exc_type, exc, traceback):
                state["inside_atomic"] = False
                return False

        def feature_state(_key):
            self.assertTrue(state["inside_atomic"])
            return True

        locked_turns = MagicMock()
        locked_turns.filter.return_value.first.return_value = None
        user = SimpleNamespace(
            pk=3,
            username="cajero",
            empleado=SimpleNamespace(
                nombre="Caja",
                apellido="Prueba",
            ),
        )
        branch = SimpleNamespace(pk=7, nombre="Sucursal")
        payment_point = SimpleNamespace(pk=21, nombre="Caja 1")
        details = [{
            "productoid": 5,
            "producto": "Producto",
            "cantidad": 1,
            "precio_unitario": Decimal("1000"),
            "subtotal": Decimal("1000"),
        }]

        with (
            patch(
                "mainApp.views.transaction.atomic",
                side_effect=lambda: AtomicGuard(),
            ),
            patch(
                "mainApp.views.locked_feature_enabled",
                side_effect=feature_state,
            ) as locked_feature,
            patch.object(
                TurnoCaja.objects,
                "select_for_update",
                return_value=locked_turns,
            ),
            patch.object(Venta.objects, "create") as create_sale,
        ):
            response = GenerarVentaView._crear_venta_ultra_fast(
                user,
                branch,
                payment_point,
                None,
                [{"medio_pago": "efectivo", "monto": Decimal("1000")}],
                details,
                Decimal("1000"),
                Decimal("1000"),
                turno=None,
                turno_requerido=False,
            )

        payload = json.loads(response.content)
        self.assertIn("control de turnos", payload["error"])
        locked_feature.assert_called_once_with(TURN_REQUIRED_FEATURE)
        create_sale.assert_not_called()
        self.assertFalse(state["inside_atomic"])

    def test_disabled_nequi_api_rejects_stale_link_before_sale_creation(self):
        from .models import NotificacionNequi, Venta

        user = SimpleNamespace(
            pk=3,
            username="cajero",
            empleado=SimpleNamespace(
                nombre="Caja",
                apellido="Prueba",
            ),
        )
        branch = SimpleNamespace(pk=7, nombre="Sucursal")
        payment_point = SimpleNamespace(pk=21, nombre="Caja 1")
        details = [{
            "productoid": 5,
            "producto": "Producto",
            "cantidad": 1,
            "precio_unitario": Decimal("1000"),
            "subtotal": Decimal("1000"),
        }]

        def feature_state(key):
            if key == TURN_REQUIRED_FEATURE:
                return False
            if key == NEQUI_API_FEATURE:
                return False
            raise AssertionError(f"Funcionalidad inesperada: {key}")

        with (
            patch(
                "mainApp.views.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch(
                "mainApp.views.locked_feature_enabled",
                side_effect=feature_state,
            ) as locked_feature,
            patch.object(
                NotificacionNequi.objects,
                "select_for_update",
            ) as lock_notification,
            patch.object(Venta.objects, "create") as create_sale,
        ):
            response = GenerarVentaView._crear_venta_ultra_fast(
                user,
                branch,
                payment_point,
                None,
                [{"medio_pago": "nequi", "monto": Decimal("1000")}],
                details,
                Decimal("1000"),
                Decimal("0"),
                turno=None,
                turno_requerido=False,
                nequi_notificacion_id=81,
            )

        payload = json.loads(response.content)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["feature_disabled"], NEQUI_API_FEATURE)
        self.assertTrue(payload["configuration_changed"])
        self.assertEqual(payload["redirect_url"], reverse("generar_venta"))
        self.assertEqual(
            [args.args[0] for args in locked_feature.call_args_list],
            [TURN_REQUIRED_FEATURE, NEQUI_API_FEATURE],
        )
        lock_notification.assert_not_called()
        create_sale.assert_not_called()


class RefundAndSpecialDiscountWithoutTurnTests(SimpleTestCase):
    def test_reintegro_turn_is_nullable_and_migration_0024_is_scoped(self):
        field = ReintegroVenta._meta.get_field("turno")

        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertIs(field.remote_field.on_delete, PROTECT)

        source = (
            settings.BASE_DIR
            / "mainApp"
            / "migrations"
            / "0024_system_feature_configuration.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            '("mainApp", "0023_special_merk2888_discount")',
            source,
        )
        self.assertEqual(source.count("migrations.CreateModel("), 2)
        self.assertEqual(source.count("migrations.AlterField("), 1)
        self.assertIn('model_name="reintegroventa"', source)
        self.assertIn("null=True", source)
        self.assertIn("on_delete=django.db.models.deletion.PROTECT", source)
        self.assertIn("seed_feature_and_permission", source)
        self.assertIn('FEATURE_KEY = "ventas_exigir_turno_caja"', source)

    def test_cash_refund_without_turn_writes_ledger_and_reduces_payment_point(self):
        sale = SimpleNamespace(
            total=Decimal("1000.00"),
            sucursalid_id=7,
            puntopagoid_id=21,
            refresh_from_db=MagicMock(),
            save=MagicMock(),
        )
        detail = SimpleNamespace(pk=31, productoid_id=5)

        with (
            patch.object(
                CambioDevolucion,
                "calcular_total_devolucion",
                return_value=Decimal("500.00"),
            ),
            patch.object(
                CambioDevolucion,
                "_turno_abierto_para_venta_locked",
            ) as active_turn,
            patch.object(
                CambioDevolucion,
                "_upsert_inventario_delta",
            ),
            patch.object(
                CambioDevolucion,
                "_upsert_turno_medio_delta",
            ) as update_turn_medium,
            patch("mainApp.models.DetalleVenta.objects.filter"),
            patch("mainApp.models.CambioDevolucion.objects.create"),
            patch(
                "mainApp.models.ReintegroVenta.objects.create",
            ) as create_refund,
            patch(
                "mainApp.models.TurnoCaja.objects.filter",
            ) as update_turn,
            patch(
                "mainApp.models.PuntosPago.objects.filter",
            ) as payment_points,
        ):
            CambioDevolucion.registrar_devolucion(
                sale,
                [{"detalle": detail, "cantidad": 1}],
                reintegro_map={"efectivo": Decimal("500.00")},
                registrado_por=SimpleNamespace(pk=3),
                turno_requerido=False,
            )

        active_turn.assert_not_called()
        create_refund.assert_called_once()
        self.assertIsNone(create_refund.call_args.kwargs["turno"])
        update_turn.assert_not_called()
        update_turn_medium.assert_not_called()
        payment_points.assert_called_once_with(pk=21)
        payment_points.return_value.update.assert_called_once()
        balance_expression = (
            payment_points.return_value.update.call_args
            .kwargs["dinerocaja"]
        )
        self.assertIn("F(dinerocaja)", repr(balance_expression))
        self.assertIn("500.00", repr(balance_expression))
        self.assertEqual(sale.total, Decimal("500.00"))

    def _special_authorization(self, fixed_now):
        return SimpleNamespace(
            pk=41,
            usada_en=None,
            venta_id=None,
            revocada_en=None,
            bloqueada_en=None,
            intentos_fallidos=0,
            expira_en=fixed_now + timedelta(minutes=15),
            cliente_especial=SimpleNamespace(cliente_id=77),
            save=MagicMock(),
        )

    def test_merk2888_accepts_null_turn_only_when_turn_is_not_required(self):
        fixed_now = datetime(
            2026,
            7,
            30,
            15,
            0,
            tzinfo=dt_timezone.utc,
        )
        without_turn = self._special_authorization(fixed_now)
        turn_required = self._special_authorization(fixed_now)
        locked_rows = MagicMock()
        locked_rows.get.side_effect = [without_turn, turn_required]
        selected_rows = MagicMock()
        selected_rows.select_for_update.return_value = locked_rows
        sale = SimpleNamespace(
            pk=100,
            clienteid_id=77,
            sucursalid_id=7,
            puntopagoid_id=21,
            total=Decimal("0.00"),
        )
        branch = SimpleNamespace(pk=7)
        cashier = SimpleNamespace(pk=3, nombreusuario="Cajero")

        with (
            patch(
                "mainApp.services.special_discount.transaction.get_connection",
                return_value=SimpleNamespace(in_atomic_block=True),
            ),
            patch(
                "mainApp.services.special_discount.timezone.now",
                return_value=fixed_now,
            ),
            patch(
                "mainApp.services.special_discount.AutorizacionDescuentoEspecial.objects.select_related",
                return_value=selected_rows,
            ),
        ):
            consumed = consume_one_time_code(
                without_turn,
                venta=sale,
                usada_por=cashier,
                turno=None,
                sucursal=branch,
                subtotal=Decimal("5000"),
                descuento=Decimal("5000"),
                turno_requerido=False,
            )

            with self.assertRaises(SpecialDiscountError) as raised:
                consume_one_time_code(
                    turn_required,
                    venta=sale,
                    usada_por=cashier,
                    turno=None,
                    sucursal=branch,
                    subtotal=Decimal("5000"),
                    descuento=Decimal("5000"),
                    turno_requerido=True,
                )

        self.assertIs(consumed, without_turn)
        self.assertIsNone(without_turn.turno)
        without_turn.save.assert_called_once()
        self.assertEqual(raised.exception.code, "shift_required")
        turn_required.save.assert_not_called()


class SalePaymentReclassificationTests(SimpleTestCase):
    def test_mixed_payment_validation_uses_original_collected_total(self):
        view = VentaDetailView()
        sale = SimpleNamespace(total=Decimal("600.00"))
        formset = SimpleNamespace(
            is_valid=lambda: True,
            cleaned_data=[
                {
                    "medio_pago": "efectivo",
                    "monto": Decimal("400.00"),
                },
                {
                    "medio_pago": "nequi",
                    "monto": Decimal("600.00"),
                },
            ],
        )

        with patch.object(
            view,
            "_venta_total_cobrado",
            return_value=Decimal("1000.00"),
        ) as collected_total:
            ok, error = view._validar_pagos_mixtos(sale, formset)

        self.assertTrue(ok)
        self.assertIsNone(error)
        collected_total.assert_called_once_with(sale)

    def test_single_payment_map_keeps_full_payment_after_refund(self):
        view = VentaDetailView()
        sale = SimpleNamespace(
            mediopago="nequi",
            total=Decimal("600.00"),
        )

        with (
            patch.object(
                view,
                "_venta_total_cobrado",
                return_value=Decimal("1000.00"),
            ),
            patch(
                "mainApp.views.PagoVenta.objects.filter",
            ) as payment_rows,
        ):
            payment_map = view._mapa_pagos_originales(sale)

        self.assertEqual(
            payment_map,
            {"nequi": Decimal("1000.00")},
        )
        payment_rows.assert_not_called()

    def test_direct_mode_applies_only_cash_difference_to_payment_point(self):
        sale = SimpleNamespace(puntopagoid_id=21)

        with (
            patch.object(
                CambioDevolucion,
                "_turno_abierto_para_venta_locked",
            ) as active_turn,
            patch.object(
                CambioDevolucion,
                "_upsert_turno_medio_delta",
            ) as update_turn_medium,
            patch(
                "mainApp.views.TurnoCaja.objects.filter",
            ) as turn_rows,
            patch(
                "mainApp.views.PuntosPago.objects.filter",
            ) as payment_points,
        ):
            VentaDetailView._aplicar_delta_mapa_pagos(
                sale,
                {"nequi": Decimal("1000.00")},
                {"efectivo": Decimal("1000.00")},
                turno_requerido=False,
            )

        active_turn.assert_not_called()
        update_turn_medium.assert_not_called()
        turn_rows.assert_not_called()
        payment_points.assert_called_once_with(pk=21)
        payment_points.return_value.update.assert_called_once()
        cash_expression = (
            payment_points.return_value.update.call_args
            .kwargs["dinerocaja"]
        )
        self.assertIn("F(dinerocaja)", repr(cash_expression))
        self.assertIn("1000.00", repr(cash_expression))

    def test_turn_mode_moves_every_medium_and_preserves_total(self):
        sale = SimpleNamespace(puntopagoid_id=21)
        turn = SimpleNamespace(pk=91)

        with (
            patch.object(
                CambioDevolucion,
                "_turno_abierto_para_venta_locked",
                return_value=turn,
            ) as active_turn,
            patch.object(
                CambioDevolucion,
                "_upsert_turno_medio_delta",
            ) as update_turn_medium,
            patch(
                "mainApp.views.TurnoCaja.objects.filter",
            ) as turn_rows,
            patch(
                "mainApp.views.PuntosPago.objects.filter",
            ) as payment_points,
        ):
            VentaDetailView._aplicar_delta_mapa_pagos(
                sale,
                {
                    "efectivo": Decimal("300.00"),
                    "nequi": Decimal("700.00"),
                },
                {
                    "efectivo": Decimal("800.00"),
                    "tarjeta": Decimal("200.00"),
                },
                turno_requerido=True,
            )

        active_turn.assert_called_once_with(sale)
        actual_deltas = {
            (item.args[1], item.args[2])
            for item in update_turn_medium.call_args_list
        }
        self.assertEqual(
            actual_deltas,
            {
                ("efectivo", Decimal("500.00")),
                ("nequi", Decimal("-700.00")),
                ("tarjeta", Decimal("200.00")),
            },
        )

        turn_rows.assert_called_once_with(pk=91)
        turn_rows.return_value.update.assert_called_once()
        turn_update = turn_rows.return_value.update.call_args.kwargs
        self.assertIn("500.00", repr(turn_update["ventas_efectivo"]))
        self.assertIn("-500.00", repr(turn_update["ventas_no_efectivo"]))

        payment_points.assert_called_once_with(pk=21)
        payment_points.return_value.update.assert_called_once()
        cash_expression = (
            payment_points.return_value.update.call_args
            .kwargs["dinerocaja"]
        )
        self.assertIn("500.00", repr(cash_expression))


class DisabledCashTurnEndpointAndUiTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _call_without_atomic_wrapper(self, view_class, request):
        handler = view_class.post
        self.assertTrue(hasattr(handler, "__wrapped__"))
        return handler.__wrapped__(view_class(), request)

    def test_both_turn_start_endpoints_reject_when_feature_is_off(self):
        request = self.factory.post(
            reverse("turno_caja_iniciar"),
            {"puntopago_id": "21", "cajero_id": "3"},
        )
        request.user = SimpleNamespace(pk=3, is_authenticated=True)

        recover_request = self.factory.post(
            reverse("turno_recuperar_o_iniciar"),
            {
                "action": "recuperar_o_iniciar",
                "puntopago_id": "21",
                "usuario_id": "3",
            },
        )
        recover_request.user = request.user

        with (
            patch(
                "mainApp.views.locked_feature_enabled",
                return_value=False,
            ),
            patch(
                "mainApp.views.PuntosPago.objects.select_for_update",
            ) as payment_points,
            patch(
                "mainApp.views._resolve_turno_cajero",
            ) as resolve_cashier,
        ):
            direct = self._call_without_atomic_wrapper(
                TurnoCajaIniciarApi,
                request,
            )
            recover = self._call_without_atomic_wrapper(
                TurnoCajaRecuperarOIniciarView,
                recover_request,
            )

        for response in (direct, recover):
            self.assertEqual(response.status_code, 409)
            payload = json.loads(response.content)
            self.assertEqual(
                payload["feature_disabled"],
                TURN_REQUIRED_FEATURE,
            )
        payment_points.assert_not_called()
        resolve_cashier.assert_not_called()

    def test_templates_javascript_and_navigation_expose_both_safe_modes(self):
        from .permissions import NAV_GROUPS, user_can_access_url_name

        base_dir = settings.BASE_DIR / "mainApp"
        sale_template = (
            base_dir / "templates" / "generar_venta.html"
        ).read_text(encoding="utf-8")
        sale_script = (
            base_dir / "static" / "javascript" / "generar_venta.js"
        ).read_text(encoding="utf-8")
        feature_template = (
            base_dir / "templates" / "configuracion_funcionalidades.html"
        ).read_text(encoding="utf-8")
        feature_script = (
            base_dir
            / "static"
            / "javascript"
            / "configuracion_funcionalidades.js"
        ).read_text(encoding="utf-8")

        self.assertIn("Modo sin turnos:", sale_template)
        self.assertIn(
            "{% if turno_requerido %}",
            sale_template,
        )
        self.assertIn(
            "window.ventaSucursalIdServidor",
            sale_template,
        )
        self.assertIn(
            "let sucursalID = serverSucursalID",
            sale_script,
        )
        self.assertIn(
            "{ sucursal_id: sucursalID, limit: 50 }",
            sale_script,
        )

        self.assertIn("Funcionalidades del sistema", feature_template)
        self.assertIn('name="password_web_master"', feature_template)
        self.assertIn('name="version"', feature_template)
        self.assertIn('name="request_id"', feature_template)
        self.assertIn("[data-feature-form]", feature_script)
        self.assertIn("[data-feature-reason]", feature_script)

        navigation_labels = [
            child["label"]
            for group in NAV_GROUPS
            for child in group.get("children", [])
        ]
        self.assertIn(
            "Funcionalidades del sistema",
            navigation_labels,
        )

        disabled_definition = feature_definition(
            TURN_REQUIRED_FEATURE
        )
        with patch(
            "mainApp.permissions.disabled_feature_for_url",
            return_value=disabled_definition,
        ):
            self.assertFalse(
                user_can_access_url_name(
                    SimpleNamespace(is_authenticated=True),
                    "turno_caja",
                )
            )


class CashTurnFeatureSecurityRegressionTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_regular_cashier_can_only_use_payment_point_from_assigned_branch(self):
        from .views import _can_use_payment_point_for_turn

        cashier = SimpleNamespace(pk=7)
        matching_point = SimpleNamespace(sucursalid_id=11)
        foreign_point = SimpleNamespace(sucursalid_id=12)

        with (
            patch("mainApp.views._require_admin", return_value=False),
            patch(
                "mainApp.views._cajero_sucursal_id",
                return_value=11,
            ),
        ):
            self.assertTrue(
                _can_use_payment_point_for_turn(
                    cashier,
                    cashier,
                    matching_point,
                )
            )
            self.assertFalse(
                _can_use_payment_point_for_turn(
                    cashier,
                    cashier,
                    foreign_point,
                )
            )

        with patch("mainApp.views._require_admin", return_value=True):
            self.assertTrue(
                _can_use_payment_point_for_turn(
                    cashier,
                    SimpleNamespace(pk=99),
                    foreign_point,
                )
            )

    def test_direct_start_rejects_foreign_payment_point_before_password(self):
        request = self.factory.post(
            "/turno_caja/api/iniciar/",
            {
                "puntopago_id": "12",
                "cajero_id": "7",
                "password": "not-evaluated",
                "saldo_apertura_efectivo": "0",
            },
        )
        request.user = SimpleNamespace(pk=7, is_authenticated=True)
        payment_point = SimpleNamespace(
            pk=12,
            sucursalid_id=99,
        )
        cashier = SimpleNamespace(pk=7)
        wrapped_post = getattr(
            TurnoCajaIniciarApi.post,
            "__wrapped__",
            TurnoCajaIniciarApi.post,
        )

        with (
            patch(
                "mainApp.views.locked_feature_enabled",
                return_value=True,
            ),
            patch(
                "mainApp.views.get_object_or_404",
                side_effect=[payment_point, cashier],
            ),
            patch(
                "mainApp.views._can_operate_cajero",
                return_value=True,
            ),
            patch(
                "mainApp.views._can_use_payment_point_for_turn",
                return_value=False,
            ) as branch_check,
            patch("mainApp.views._password_ok") as password_check,
        ):
            response = wrapped_post(TurnoCajaIniciarApi(), request)

        self.assertEqual(response.status_code, 403)
        branch_check.assert_called_once_with(
            request.user,
            cashier,
            payment_point,
        )
        password_check.assert_not_called()

    def test_admin_cannot_reopen_turn_while_direct_mode_is_active(self):
        from .models import TurnoCaja
        from .views import TurnoCajaAdminUpdateAPI

        request = self.factory.post(
            "/api/admin/turnos_caja/45/update/",
            data=json.dumps({"estado": "ABIERTO"}),
            content_type="application/json",
        )
        request.user = SimpleNamespace(pk=1, is_authenticated=True)
        wrapped_post = getattr(
            TurnoCajaAdminUpdateAPI.post,
            "__wrapped__",
            TurnoCajaAdminUpdateAPI.post,
        )

        with (
            patch("mainApp.views._can_edit_turnos", return_value=True),
            patch(
                "mainApp.views.locked_feature_enabled",
                return_value=False,
            ) as feature_lock,
            patch.object(
                TurnoCaja.objects,
                "select_for_update",
            ) as turn_lock,
        ):
            response = wrapped_post(
                TurnoCajaAdminUpdateAPI(),
                request,
                45,
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            json.loads(response.content)["feature_disabled"],
            TURN_REQUIRED_FEATURE,
        )
        feature_lock.assert_called_once_with(TURN_REQUIRED_FEATURE)
        turn_lock.assert_not_called()

    def test_ui_does_not_restore_shared_payment_point_or_depend_on_javascript(self):
        base_dir = settings.BASE_DIR / "mainApp"
        sale_script = (
            base_dir / "static" / "javascript" / "generar_venta.js"
        ).read_text(encoding="utf-8")
        sale_view = (
            base_dir / "views.py"
        ).read_text(encoding="utf-8")
        feature_template = (
            base_dir / "templates" / "configuracion_funcionalidades.html"
        ).read_text(encoding="utf-8")
        feature_script = (
            base_dir
            / "static"
            / "javascript"
            / "configuracion_funcionalidades.js"
        ).read_text(encoding="utf-8")
        feature_css = (
            base_dir
            / "static"
            / "css"
            / "configuracion_funcionalidades.css"
        ).read_text(encoding="utf-8")

        self.assertNotIn("const savedPunto", sale_script)
        self.assertIn(
            '$("#puntopago_autocomplete").on("input"',
            sale_script,
        )
        self.assertIn("configuration_changed", sale_script)
        self.assertIn("puntos_disponibles = bool(puntos)", sale_view)
        self.assertIn('name="reason"', feature_template)
        self.assertNotIn("data-feature-reason-alias", feature_template)
        self.assertIn("data-feature-disable-message", feature_template)
        self.assertIn("form.dataset.featureDisableMessage", feature_script)
        self.assertIn("overflow-y:auto", feature_css)


class RoutePermissionAndNavigationCoverageTests(SimpleTestCase):
    """Evita que una URL nueva quede fuera del permiso o del navbar."""

    @staticmethod
    def _url_patterns():
        from django.urls.resolvers import URLPattern, URLResolver
        from .urls import urlpatterns

        def walk(patterns):
            for pattern in patterns:
                if isinstance(pattern, URLPattern):
                    yield pattern
                elif isinstance(pattern, URLResolver):
                    yield from walk(pattern.url_patterns)

        return list(walk(urlpatterns))

    @staticmethod
    def _nav_url_names():
        from .permissions import NAV_GROUPS

        names = set()
        for group in NAV_GROUPS:
            if group.get("url_name"):
                names.add(group["url_name"])
            names.update(
                child["url_name"]
                for child in group.get("children", [])
                if child.get("url_name")
            )
        return names

    def test_every_named_app_route_has_an_explicit_access_policy(self):
        from .permissions import (
            ALWAYS_ALLOWED_URL_NAMES,
            PUBLIC_URL_NAMES,
            ROUTE_PERMISSIONS,
        )

        route_names = {
            pattern.name
            for pattern in self._url_patterns()
            if pattern.name
        }
        classified = (
            set(ROUTE_PERMISSIONS)
            | set(PUBLIC_URL_NAMES)
            | set(ALWAYS_ALLOWED_URL_NAMES)
        )

        self.assertEqual(sorted(route_names - classified), [])

    def test_route_names_are_unique(self):
        from collections import Counter

        names = [
            pattern.name
            for pattern in self._url_patterns()
            if pattern.name
        ]
        duplicates = sorted(
            name
            for name, count in Counter(names).items()
            if count > 1
        )

        self.assertEqual(duplicates, [])

    def test_every_permission_mapping_uses_a_catalog_code(self):
        from .permissions import (
            PERMISSION_BY_CODE,
            ROUTE_PERMISSION_ALTERNATIVES,
            ROUTE_PERMISSIONS,
        )

        mapped_codes = set(ROUTE_PERMISSIONS.values())
        mapped_codes.update(
            code
            for alternatives in ROUTE_PERMISSION_ALTERNATIVES.values()
            for code in alternatives
        )

        self.assertEqual(sorted(mapped_codes - set(PERMISSION_BY_CODE)), [])

    def test_every_static_html_page_is_represented_in_the_navbar(self):
        from .permissions import PUBLIC_URL_NAMES

        page_names = set()
        for pattern in self._url_patterns():
            route = str(pattern.pattern)
            view_class = getattr(pattern.callback, "view_class", None)
            if (
                not pattern.name
                or pattern.name in PUBLIC_URL_NAMES
                or "<" in route
                or view_class is None
                or not callable(getattr(view_class, "get", None))
                or not getattr(view_class, "template_name", None)
            ):
                continue
            page_names.add(pattern.name)

        self.assertEqual(
            sorted(page_names - self._nav_url_names()),
            [],
        )

    def test_every_nav_link_resolves_to_a_get_page(self):
        for url_name in self._nav_url_names():
            match = resolve(reverse(url_name))
            view_class = getattr(match.func, "view_class", None)
            self.assertIsNotNone(view_class, url_name)
            self.assertTrue(
                callable(getattr(view_class, "get", None)),
                url_name,
            )

    def test_web_master_bypasses_individual_permission_denials(self):
        from .permissions import user_can_access_url_name

        user = SimpleNamespace(is_authenticated=True)
        with (
            patch(
                "mainApp.permissions.disabled_feature_for_url",
                return_value=None,
            ),
            patch(
                "mainApp.permissions.is_web_master_role",
                return_value=True,
            ),
            patch(
                "mainApp.permissions.user_has_permission",
                return_value=False,
            ) as permission_check,
        ):
            allowed = user_can_access_url_name(
                user,
                "visualizar_productos",
            )

        self.assertTrue(allowed)
        permission_check.assert_not_called()

    def test_disabled_feature_still_stays_hidden_from_web_master(self):
        from .permissions import user_can_access_url_name

        with (
            patch(
                "mainApp.permissions.disabled_feature_for_url",
                return_value={"key": TURN_REQUIRED_FEATURE},
            ),
            patch(
                "mainApp.permissions.is_web_master_role",
                return_value=True,
            ),
        ):
            allowed = user_can_access_url_name(
                SimpleNamespace(is_authenticated=True),
                "turno_caja",
            )

        self.assertFalse(allowed)

    def test_nav_cache_changes_with_feature_configuration(self):
        from .permissions import _nav_cache_key

        user = SimpleNamespace(
            pk=7,
            rolid_id=1,
            is_staff=False,
            is_superuser=False,
        )
        with patch(
            "mainApp.permissions.is_feature_enabled",
            return_value=True,
        ):
            enabled_key = _nav_cache_key(user)
        with patch(
            "mainApp.permissions.is_feature_enabled",
            return_value=False,
        ):
            disabled_key = _nav_cache_key(user)

        self.assertNotEqual(enabled_key, disabled_key)

    def test_only_the_most_specific_nav_link_is_active(self):
        from .permissions import _mark_nav_active

        menu = [
            {"label": "Turno", "url": "/turno_caja/", "children": []},
            {
                "label": "Retiro",
                "url": "/turno_caja/retiro/",
                "children": [],
            },
        ]

        marked = _mark_nav_active(menu, "/turno_caja/retiro/18/")

        self.assertFalse(marked[0]["active"])
        self.assertTrue(marked[1]["active"])


class PermissionMiddlewareCoverageTests(SimpleTestCase):
    def setUp(self):
        from .middleware import PagePermissionMiddleware

        self.factory = RequestFactory()
        self.middleware = PagePermissionMiddleware(
            lambda request: HttpResponse("ok")
        )

    @staticmethod
    def _future_internal_view(request):
        return HttpResponse("future")

    def _request(self):
        request = self.factory.get(
            "/future-internal/",
            HTTP_ACCEPT="application/json",
        )
        request.user = SimpleNamespace(is_authenticated=True)
        request.resolver_match = SimpleNamespace(
            url_name="future_internal_route",
        )
        return request

    def test_unclassified_internal_route_fails_closed(self):
        with patch(
            "mainApp.middleware.is_web_master_role",
            return_value=False,
        ):
            response = self.middleware.process_view(
                self._request(),
                self._future_internal_view,
                (),
                {},
            )

        self.assertEqual(response.status_code, 403)

    def test_web_master_can_reach_future_unclassified_internal_route(self):
        with patch(
            "mainApp.middleware.is_web_master_role",
            return_value=True,
        ):
            response = self.middleware.process_view(
                self._request(),
                self._future_internal_view,
                (),
                {},
            )

        self.assertIsNone(response)


class WebMasterPermissionGrantTests(SimpleTestCase):
    def test_grants_every_web_master_role_without_assuming_primary_key_one(self):
        from .permissions import grant_all_permissions_to_web_master

        web_master = SimpleNamespace(pk=27, nombre="Web Master")
        regular_role = SimpleNamespace(pk=1, nombre="Administrador")
        permissions = [
            SimpleNamespace(pk=10),
            SimpleNamespace(pk=11),
        ]
        role_permission_model = MagicMock()
        role_permission_model.objects.filter.return_value.values_list.return_value = []

        with (
            patch("mainApp.permissions.sync_permission_catalog"),
            patch(
                "mainApp.permissions.Rol.objects.all",
                return_value=[regular_role, web_master],
            ),
            patch(
                "mainApp.permissions.Permiso.objects.all",
                return_value=permissions,
            ),
            patch(
                "mainApp.permissions.RolPermiso",
                role_permission_model,
            ),
            patch(
                "mainApp.permissions._bump_permission_cache_version",
            ) as bump_cache,
        ):
            granted = grant_all_permissions_to_web_master()

        self.assertEqual(granted, 2)
        role_permission_model.objects.filter.assert_called_once_with(
            rol=web_master,
        )
        role_permission_model.objects.bulk_create.assert_called_once()
        bump_cache.assert_called_once_with()
