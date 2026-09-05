from contextlib import nullcontext
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.db import DatabaseError, models
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve, reverse

from .models import CambioDevolucion, MetodoPago
from .permissions import WEB_MASTER_ONLY_URL_NAMES, route_permission_for_url_name
from .services.payment_methods import (
    CASH_PAYMENT_CODE,
    DEFAULT_PAYMENT_METHODS,
    INTERNAL_PAYMENT_CODES,
    PaymentMethodCodeError,
    active_payment_method_codes,
    normalize_payment_method_code,
    payment_method_options,
    validate_new_payment_method_code,
)
from .views import (
    ConfiguracionMetodosPagoView,
    GenerarVentaView,
    _turn_payment_method_codes,
)


class PaymentMethodCatalogServiceTests(SimpleTestCase):
    @staticmethod
    def _row(
        code,
        label,
        *,
        active=True,
        is_cash=False,
        is_system=False,
        order=100,
        version=1,
    ):
        return SimpleNamespace(
            codigo=code,
            nombre=label,
            activo=active,
            es_efectivo=is_cash,
            es_sistema=is_system,
            orden=order,
            version=version,
            actualizado_en=None,
            actualizado_por_nombre="",
        )

    def test_service_returns_active_methods_and_explicit_inactive_history(self):
        cash = self._row(
            "efectivo",
            "Efectivo",
            is_cash=True,
            is_system=True,
            order=10,
        )
        historical = self._row(
            "bono_antiguo",
            "Bono antiguo",
            active=False,
            order=90,
            version=4,
        )

        all_rows = MagicMock()
        filtered_rows = MagicMock()
        all_rows.filter.return_value = filtered_rows
        filtered_rows.order_by.return_value = [cash, historical]

        with (
            patch(
                "mainApp.services.payment_methods.payment_method_table_ready",
                return_value=True,
            ),
            patch.object(MetodoPago.objects, "all", return_value=all_rows),
        ):
            options = payment_method_options(
                active_only=True,
                include_codes=["BONO ANTIGUO"],
            )

        self.assertEqual(
            [(row["code"], row["active"]) for row in options],
            [("efectivo", True), ("bono_antiguo", False)],
        )
        all_rows.filter.assert_called_once()

    def test_service_returns_all_methods_for_historical_filters(self):
        active = self._row("efectivo", "Efectivo", is_cash=True, order=10)
        inactive = self._row(
            "credito_tienda",
            "Credito tienda",
            active=False,
            order=80,
        )
        queryset = MagicMock()
        queryset.order_by.return_value = [active, inactive]

        with (
            patch(
                "mainApp.services.payment_methods.payment_method_table_ready",
                return_value=True,
            ),
            patch.object(MetodoPago.objects, "all", return_value=queryset),
        ):
            options = payment_method_options(active_only=False)

        self.assertEqual(
            {row["code"] for row in options},
            {"efectivo", "credito_tienda"},
        )
        queryset.filter.assert_not_called()

    def test_active_code_helper_excludes_inactive_historical_rows(self):
        with patch(
            "mainApp.services.payment_methods.payment_method_options",
            return_value=[
                {"code": "efectivo", "active": True},
                {"code": "bono_antiguo", "active": False},
                {"code": "credito_tienda", "active": True},
            ],
        ):
            codes = active_payment_method_codes()

        self.assertEqual(codes, {"efectivo", "credito_tienda"})

    def test_missing_catalog_uses_legacy_defaults_and_preserves_requested_history(self):
        with patch(
            "mainApp.services.payment_methods.payment_method_table_ready",
            return_value=False,
        ):
            options = payment_method_options(
                active_only=True,
                include_codes=["Bono Histórico"],
            )

        default_codes = {row["code"] for row in DEFAULT_PAYMENT_METHODS}
        by_code = {row["code"]: row for row in options}
        self.assertTrue(default_codes.issubset(by_code))
        self.assertNotIn("banco_caja_social", by_code)
        self.assertEqual(
            by_code["tarjeta"]["label"],
            "Tarjeta / Banco Caja Social",
        )
        self.assertIn("bono_historico", by_code)
        self.assertFalse(by_code["bono_historico"]["active"])

    def test_database_error_is_not_hidden_once_catalog_is_reported_ready(self):
        with (
            patch(
                "mainApp.services.payment_methods.payment_method_table_ready",
                return_value=True,
            ),
            patch.object(
                MetodoPago.objects,
                "all",
                side_effect=DatabaseError("database unavailable"),
            ),
        ):
            with self.assertRaises(DatabaseError):
                payment_method_options(active_only=True)

    def test_normalization_is_stable_and_collapses_legacy_aliases(self):
        cases = {
            "  Crédito tienda  ": "credito_tienda",
            "DAVI PLATA": "daviplata",
            "cash": "efectivo",
            "TC": "tarjeta",
            "Caja Social": "tarjeta",
            "Banco-Caja/Social": "tarjeta",
        }

        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_payment_method_code(raw), expected)

    def test_internal_pseudo_methods_are_not_catalog_payment_options(self):
        self.assertEqual(
            INTERNAL_PAYMENT_CODES,
            {"mixto", "sin_pago", "facturas_pagadas"},
        )
        with patch(
            "mainApp.services.payment_methods.payment_method_table_ready",
            return_value=False,
        ):
            options = payment_method_options(
                active_only=True,
                include_codes=INTERNAL_PAYMENT_CODES,
            )

        self.assertTrue(
            INTERNAL_PAYMENT_CODES.isdisjoint(
                {row["code"] for row in options}
            )
        )

    def test_new_codes_reject_internal_system_and_legacy_alias_tokens(self):
        for reserved in (
            "mixto",
            "sin pago",
            "facturas_pagadas",
            "efectivo",
            "cash",
            "tc",
            "credito",
            "davi plata",
            "bcs",
            "banco caja social",
        ):
            with self.subTest(reserved=reserved):
                with self.assertRaises(PaymentMethodCodeError):
                    validate_new_payment_method_code(reserved)

        self.assertEqual(
            validate_new_payment_method_code("  Bono de regalo  "),
            "bono_de_regalo",
        )


class PaymentMethodModelContractTests(SimpleTestCase):
    def test_model_uses_standalone_catalog_without_legacy_foreign_keys(self):
        self.assertEqual(MetodoPago._meta.db_table, "metodos_pago")
        self.assertEqual(
            MetodoPago._meta.ordering,
            ["orden", "nombre", "codigo"],
        )

        code_field = MetodoPago._meta.get_field("codigo")
        self.assertTrue(code_field.primary_key)
        self.assertEqual(code_field.max_length, 50)
        self.assertFalse(any(
            isinstance(field, (models.ForeignKey, models.OneToOneField))
            and field.name not in {"actualizado_por"}
            for field in MetodoPago._meta.fields
        ))

    def test_model_constraints_protect_version_and_single_cash_method(self):
        constraints = {
            constraint.name: constraint
            for constraint in MetodoPago._meta.constraints
        }

        self.assertIn("metodo_pago_version_positiva", constraints)
        self.assertIsInstance(
            constraints["metodo_pago_version_positiva"],
            models.CheckConstraint,
        )

        cash_constraint = constraints["metodo_pago_un_solo_efectivo"]
        self.assertIsInstance(cash_constraint, models.UniqueConstraint)
        self.assertEqual(tuple(cash_constraint.fields), ("es_efectivo",))
        self.assertIsNotNone(cash_constraint.condition)

    def test_migration_only_creates_and_seeds_catalog(self):
        migration = (
            Path(settings.BASE_DIR)
            / "mainApp"
            / "migrations"
            / "0028_metodopago.py"
        ).read_text(encoding="utf-8")

        self.assertIn('("mainApp", "0027_configuracion_impresion_corte_automatico")', migration)
        self.assertEqual(migration.count("migrations.CreateModel("), 1)
        self.assertNotIn("migrations.AlterField(", migration)
        self.assertNotIn("migrations.AddField(", migration)
        self.assertIn("migrations.RunPython.noop", migration)
        for code in (
            "efectivo",
            "nequi",
            "daviplata",
            "tarjeta",
            "banco_caja_social",
        ):
            self.assertIn(f'("{code}",', migration)


class PaymentMethodConfigurationPageTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.url = reverse("configuracion_metodos_pago")

    @staticmethod
    def _user(*, password_valid=True):
        return SimpleNamespace(
            pk=91,
            is_authenticated=True,
            is_active=True,
            nombreusuario="William Nova",
            check_password=MagicMock(return_value=password_valid),
        )

    def test_route_permission_and_access_are_strictly_web_master_only(self):
        self.assertEqual(resolve(self.url).url_name, "configuracion_metodos_pago")
        self.assertEqual(
            route_permission_for_url_name("configuracion_metodos_pago"),
            "configuracion_metodos_pago",
        )
        self.assertIn("configuracion_metodos_pago", WEB_MASTER_ONLY_URL_NAMES)

        anonymous_request = self.factory.get(self.url)
        anonymous_request.user = SimpleNamespace(is_authenticated=False)
        anonymous = ConfiguracionMetodosPagoView.as_view()(anonymous_request)
        self.assertEqual(anonymous.status_code, 302)

        denied_request = self.factory.get(self.url)
        denied_request.user = self._user()
        with patch("mainApp.views.is_web_master_role", return_value=False):
            denied = ConfiguracionMetodosPagoView.as_view()(denied_request)
        self.assertEqual(denied.status_code, 403)

        allowed_request = self.factory.get(self.url)
        allowed_request.user = self._user()
        with (
            patch("mainApp.views.is_web_master_role", return_value=True),
            patch.object(
                ConfiguracionMetodosPagoView,
                "get",
                return_value=HttpResponse("ok"),
            ),
        ):
            allowed = ConfiguracionMetodosPagoView.as_view()(allowed_request)
        self.assertEqual(allowed.status_code, 200)

    def test_wrong_password_never_opens_transaction_or_writes(self):
        user = self._user(password_valid=False)
        request = self.factory.post(self.url, {
            "action": "create",
            "label": "Bono de regalo",
            "order": "70",
            "password_web_master": "incorrecta",
        })
        request.user = user

        with (
            patch("mainApp.views.is_web_master_role", return_value=True),
            patch("mainApp.views.payment_method_table_ready", return_value=True),
            patch.object(
                ConfiguracionMetodosPagoView,
                "_render",
                return_value=HttpResponse("password incorrecta", status=400),
            ) as render_view,
            patch("mainApp.views.transaction.atomic") as atomic,
            patch.object(MetodoPago.objects, "create") as create_method,
        ):
            response = ConfiguracionMetodosPagoView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        user.check_password.assert_called_once_with("incorrecta")
        render_view.assert_called_once()
        atomic.assert_not_called()
        create_method.assert_not_called()

    def test_catalog_exposes_soft_toggle_instead_of_destructive_delete(self):
        user = self._user(password_valid=True)
        request = self.factory.post(self.url, {
            "action": "delete",
            "code": "bono_antiguo",
            "version": "4",
            "password_web_master": "correcta",
        })
        request.user = user

        with (
            patch("mainApp.views.is_web_master_role", return_value=True),
            patch("mainApp.views.payment_method_table_ready", return_value=True),
            patch.object(
                ConfiguracionMetodosPagoView,
                "_render",
                return_value=HttpResponse("accion invalida", status=400),
            ),
            patch("mainApp.views.transaction.atomic") as atomic,
            patch.object(MetodoPago.objects, "filter") as method_filter,
        ):
            response = ConfiguracionMetodosPagoView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        atomic.assert_not_called()
        method_filter.assert_not_called()

    def test_valid_password_creates_normalized_active_custom_method(self):
        user = self._user(password_valid=True)
        request = self.factory.post(self.url, {
            "action": "create",
            "label": "  Bono de regalo  ",
            "order": "70",
            "password_web_master": "correcta",
        })
        request.user = user
        duplicates = MagicMock()
        duplicates.exists.return_value = False

        with (
            patch("mainApp.views.is_web_master_role", return_value=True),
            patch("mainApp.views.payment_method_table_ready", return_value=True),
            patch("mainApp.views.transaction.atomic", return_value=nullcontext()),
            patch.object(
                MetodoPago.objects,
                "filter",
                return_value=duplicates,
            ),
            patch.object(MetodoPago.objects, "create") as create_method,
            patch("mainApp.views.messages.success") as success_message,
        ):
            response = ConfiguracionMetodosPagoView.as_view()(request)

        self.assertEqual(response.status_code, 302)
        user.check_password.assert_called_once_with("correcta")
        create_method.assert_called_once_with(
            codigo="bono_de_regalo",
            nombre="Bono de regalo",
            activo=True,
            es_efectivo=False,
            es_sistema=False,
            orden=70,
            version=1,
            actualizado_por=user,
            actualizado_por_nombre="William Nova",
        )
        success_message.assert_called_once()

    def test_toggle_deactivates_and_reactivates_same_catalog_row(self):
        view = ConfiguracionMetodosPagoView()
        user = self._user()
        method = SimpleNamespace(
            pk="bono_de_regalo",
            codigo="bono_de_regalo",
            nombre="Bono de regalo",
            activo=True,
            es_efectivo=False,
            version=3,
            save=MagicMock(),
        )
        active_rows = MagicMock()
        active_rows.count.return_value = 2
        locked_rows = MagicMock()
        locked_rows.filter.return_value = active_rows

        deactivate = self.factory.post(self.url, {"active": "0"})
        deactivate.user = user
        with (
            patch.object(view, "_locked_method", return_value=method),
            patch.object(
                MetodoPago.objects,
                "select_for_update",
                return_value=locked_rows,
            ),
        ):
            view._toggle(deactivate)

        self.assertFalse(method.activo)
        self.assertEqual(method.version, 4)
        method.save.assert_called_once()

        method.save.reset_mock()
        reactivate = self.factory.post(self.url, {"active": "1"})
        reactivate.user = user
        with patch.object(view, "_locked_method", return_value=method):
            view._toggle(reactivate)

        self.assertTrue(method.activo)
        self.assertEqual(method.version, 5)
        method.save.assert_called_once()

    def test_cash_method_cannot_be_deactivated(self):
        view = ConfiguracionMetodosPagoView()
        request = self.factory.post(self.url, {"active": "0"})
        request.user = self._user()
        cash = SimpleNamespace(
            pk=CASH_PAYMENT_CODE,
            nombre="Efectivo",
            activo=True,
            es_efectivo=True,
            version=8,
            save=MagicMock(),
        )

        with patch.object(view, "_locked_method", return_value=cash):
            with self.assertRaisesMessage(ValueError, "no puede desactivarse"):
                view._toggle(request)

        cash.save.assert_not_called()

    def test_stale_version_returns_conflict_without_mutating_method(self):
        user = self._user(password_valid=True)
        request = self.factory.post(self.url, {
            "action": "toggle",
            "code": "bono_de_regalo",
            "active": "0",
            "version": "2",
            "password_web_master": "correcta",
        })
        request.user = user
        method = SimpleNamespace(
            pk="bono_de_regalo",
            nombre="Bono de regalo",
            activo=True,
            es_efectivo=False,
            version=3,
            save=MagicMock(),
        )
        locked_rows = MagicMock()
        locked_rows.filter.return_value.first.return_value = method

        def render_error(_view, request, *, error="", status=200):
            return HttpResponse(error, status=status)

        with (
            patch("mainApp.views.is_web_master_role", return_value=True),
            patch("mainApp.views.payment_method_table_ready", return_value=True),
            patch("mainApp.views.transaction.atomic", return_value=nullcontext()),
            patch.object(
                MetodoPago.objects,
                "select_for_update",
                return_value=locked_rows,
            ),
            patch.object(
                ConfiguracionMetodosPagoView,
                "_render",
                autospec=True,
                side_effect=render_error,
            ),
        ):
            response = ConfiguracionMetodosPagoView.as_view()(request)

        self.assertEqual(response.status_code, 409)
        self.assertIn("Otro usuario", response.content.decode("utf-8"))
        method.save.assert_not_called()


class DynamicSalePaymentValidationTests(SimpleTestCase):
    def test_sale_accepts_active_custom_method(self):
        result = GenerarVentaView._normalize_payments(
            [{"medio_pago": "credito_tienda", "monto": "1000"}],
            Decimal("1000"),
            allowed_codes={"efectivo", "credito_tienda"},
        )

        self.assertEqual(result, [{
            "medio_pago": "credito_tienda",
            "monto": Decimal("1000"),
        }])

    def test_sale_rejects_inactive_and_unknown_methods(self):
        active_codes = {"efectivo", "tarjeta"}
        for code in ("bono_inactivo", "metodo_desconocido"):
            with self.subTest(code=code):
                result = GenerarVentaView._normalize_payments(
                    [{"medio_pago": code, "monto": "1000"}],
                    Decimal("1000"),
                    allowed_codes=active_codes,
                )
                self.assertEqual(result, [])

    def test_simple_custom_payment_obeys_same_active_catalog(self):
        accepted = GenerarVentaView._normalize_payments(
            [],
            Decimal("2500"),
            "bono_de_regalo",
            allowed_codes={"efectivo", "bono_de_regalo"},
        )
        rejected = GenerarVentaView._normalize_payments(
            [],
            Decimal("2500"),
            "bono_de_regalo",
            allowed_codes={"efectivo"},
        )

        self.assertEqual(accepted, [{
            "medio_pago": "bono_de_regalo",
            "monto": Decimal("2500"),
        }])
        self.assertEqual(rejected, [])


class DynamicRefundPaymentValidationTests(SimpleTestCase):
    def test_refund_accepts_active_custom_method_after_normalization(self):
        with patch(
            "mainApp.services.payment_methods.active_payment_method_codes",
            return_value={"efectivo", "credito_tienda"},
        ):
            result = CambioDevolucion._normalizar_reintegro_map(
                {"Crédito tienda": Decimal("500")},
                Decimal("500"),
            )

        self.assertEqual(result, {"credito_tienda": Decimal("500.00")})

    def test_refund_rejects_inactive_custom_method(self):
        with patch(
            "mainApp.services.payment_methods.active_payment_method_codes",
            return_value={"efectivo"},
        ):
            with self.assertRaisesMessage(ValueError, "no válido"):
                CambioDevolucion._normalizar_reintegro_map(
                    {"credito_tienda": Decimal("500")},
                    Decimal("500"),
                )

    def test_empty_refund_distribution_still_defaults_to_cash(self):
        with patch(
            "mainApp.services.payment_methods.active_payment_method_codes",
            return_value=set(),
        ):
            result = CambioDevolucion._normalizar_reintegro_map(
                {},
                Decimal("500"),
            )

        self.assertEqual(result, {"efectivo": Decimal("500.00")})


class DynamicCashTurnPaymentCatalogTests(SimpleTestCase):
    def test_turn_order_combines_active_movement_and_historical_methods(self):
        with patch(
            "mainApp.views.payment_method_options",
            return_value=[
                {"code": "efectivo"},
                {"code": "bono_activo"},
                {"code": "credito_historico"},
                {"code": "vale_movimiento"},
            ],
        ) as options:
            codes = _turn_payment_method_codes(
                expected={
                    "Vale movimiento": Decimal("200.00"),
                    "facturas_pagadas": Decimal("10.00"),
                },
                existing_codes=["credito_historico", "mixto"],
            )

        self.assertEqual(
            codes,
            [
                "efectivo",
                "bono_activo",
                "credito_historico",
                "vale_movimiento",
            ],
        )
        self.assertTrue(INTERNAL_PAYMENT_CODES.isdisjoint(codes))
        options.assert_called_once_with(
            active_only=True,
            include_codes=["vale_movimiento", "credito_historico"],
        )

    def test_cash_is_always_present_even_with_incomplete_catalog(self):
        with patch(
            "mainApp.views.payment_method_options",
            return_value=[{"code": "bono_activo"}],
        ):
            codes = _turn_payment_method_codes()

        self.assertEqual(codes, ["efectivo", "bono_activo"])
