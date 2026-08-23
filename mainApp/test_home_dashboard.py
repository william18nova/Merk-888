from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase
from django.template.loader import render_to_string

from .views import HomePageView


class EmployeeHomeDashboardTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.permissions = {
            "sales_summary": True,
            "sales_products": False,
            "cash_turn": False,
            "inventory_alerts": False,
            "product_quality": False,
            "orders": False,
        }

    def _context_for(
        self,
        user,
        *,
        web_master=False,
        permission_admin=False,
    ):
        view = HomePageView()
        view.request = self.factory.get("/")
        view.request.user = user
        with (
            patch.object(
                view,
                "_dashboard_permissions",
                return_value=self.permissions,
            ),
            patch.object(view, "_quick_actions", return_value=[]),
            patch("mainApp.views.user_can_access_url_name", return_value=False),
            patch("mainApp.views.is_feature_enabled", return_value=False),
            patch("mainApp.views.is_web_master_role", return_value=web_master),
            patch(
                "mainApp.views.is_permission_admin",
                return_value=permission_admin,
            ),
            patch("mainApp.views.Venta.objects.filter") as sales_filter,
        ):
            sales_filter.return_value.aggregate.return_value = {
                "total": 125000,
                "cantidad": 8,
            }
            context = view.get_context_data()
        return context, sales_filter

    def test_employee_home_does_not_query_or_show_daily_sales_total(self):
        employee = SimpleNamespace(__str__=lambda _self: "Empleado")
        user = SimpleNamespace(empleado=employee, __str__=lambda _self: "usuario")

        context, sales_filter = self._context_for(user)

        self.assertFalse(context["dashboard_show_sales_summary"])
        self.assertTrue(context["dashboard_is_employee"])
        self.assertNotIn(
            "Ventas de hoy",
            [card["label"] for card in context["dashboard_cards"]],
        )
        sales_filter.assert_not_called()

    def test_privileged_admin_keeps_authorized_sales_summary(self):
        user = SimpleNamespace(empleado=None, __str__=lambda _self: "administrador")

        context, sales_filter = self._context_for(user, permission_admin=True)

        self.assertTrue(context["dashboard_show_sales_summary"])
        self.assertIn(
            "Ventas de hoy",
            [card["label"] for card in context["dashboard_cards"]],
        )
        sales_filter.assert_called_once()

    def test_web_master_keeps_summary_even_with_employee_profile(self):
        employee = SimpleNamespace(__str__=lambda _self: "Web Master")
        user = SimpleNamespace(empleado=employee, __str__=lambda _self: "wm")

        context, sales_filter = self._context_for(user, web_master=True)

        self.assertFalse(context["dashboard_is_employee"])
        self.assertTrue(context["dashboard_show_sales_summary"])
        self.assertIn(
            "Ventas de hoy",
            [card["label"] for card in context["dashboard_cards"]],
        )
        sales_filter.assert_called_once()

    def test_employee_home_omits_all_sales_information_and_queries(self):
        employee = SimpleNamespace(nombre="Empleado")
        user = SimpleNamespace(empleado=employee)
        permissions = {
            **self.permissions,
            "sales_products": True,
            "cash_turn": True,
        }
        view = HomePageView()
        view.request = self.factory.get("/")
        view.request.user = user

        with (
            patch.object(
                view,
                "_dashboard_permissions",
                return_value=permissions,
            ),
            patch("mainApp.views.user_can_access_url_name", return_value=True),
            patch(
                "mainApp.views.is_feature_enabled",
                return_value=True,
            ) as feature_enabled,
            patch("mainApp.views.is_web_master_role", return_value=False),
            patch("mainApp.views.is_permission_admin", return_value=False),
            patch(
                "mainApp.views.reverse",
                side_effect=lambda name: f"/{name}/",
            ),
            patch("mainApp.views.Venta.objects.filter") as sales_filter,
            patch("mainApp.views.NotificacionNequi.objects.filter") as nequi_filter,
            patch("mainApp.views.DetalleVenta.objects.filter") as product_filter,
            patch("mainApp.views.TurnoCaja.objects.select_related") as turn_filter,
        ):
            context = view.get_context_data()

        sales_filter.assert_not_called()
        nequi_filter.assert_not_called()
        product_filter.assert_not_called()
        turn_filter.assert_not_called()
        feature_enabled.assert_not_called()

        card_labels = {card["label"] for card in context["dashboard_cards"]}
        self.assertTrue(
            card_labels.isdisjoint({"Ventas de hoy", "Nequi hoy", "Turno actual"})
        )
        self.assertFalse(context["dashboard_show_sales_summary"])
        self.assertFalse(context["dashboard_show_top_products"])
        self.assertEqual(context["dashboard_top_products"], [])
        self.assertIsNone(context["dashboard_turno"])
        self.assertFalse(context["dashboard_turno_requerido"])
        self.assertEqual(context["dashboard_turno_label"], "Sesion activa")

        quick_action_labels = {
            action["label"] for action in context["dashboard_quick_actions"]
        }
        self.assertIn("Generar venta", quick_action_labels)
        self.assertIn("Turno de caja", quick_action_labels)
        self.assertTrue(
            quick_action_labels.isdisjoint({
                "Notificaciones Nequi",
                "Ventas diarias",
                "Metricas del negocio",
            })
        )

        html = render_to_string(
            "homePage.html",
            {
                **context,
                "request": SimpleNamespace(
                    resolver_match=SimpleNamespace(url_name="login"),
                ),
            },
        )
        for hidden_copy in (
            "Ventas de hoy",
            "Nequi hoy",
            "Turno actual",
            "Productos mas vendidos hoy",
            "Notificaciones Nequi",
            "Ventas diarias",
            "Metricas del negocio",
        ):
            self.assertNotIn(hidden_copy, html)
        self.assertIn("Generar venta", html)
        self.assertIn("Turno de caja", html)

    def test_unlinked_operational_user_is_also_protected(self):
        user = SimpleNamespace(empleado=None)

        context, sales_filter = self._context_for(user)

        self.assertTrue(context["dashboard_is_employee"])
        self.assertFalse(context["dashboard_show_sales_summary"])
        self.assertNotIn(
            "Ventas de hoy",
            [card["label"] for card in context["dashboard_cards"]],
        )
        sales_filter.assert_not_called()
