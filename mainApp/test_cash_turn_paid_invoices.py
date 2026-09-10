"""Pagos de caja: primer paso del cierre, sin duplicar el cuadre."""

import json
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.template import Context, Engine
from django.urls import reverse
from django.utils import timezone

from .models import PuntosPago, Sucursal, TurnoCaja, Usuario
from .views import (
    TurnoCajaAdminDetailAPI, TurnoCajaAdminUpdateAPI, TurnoCajaCerrarApi,
    TurnoCajaCierrePageView, TurnoCajaPageView,
)


def render_close_page(page):
    engine = Engine(
        dirs=[settings.BASE_DIR / "mainApp/templates"],
        loaders=[("django.template.loaders.locmem.Loader", {
            "base.html": "{% block extra_head %}{% endblock %}{% block content %}{% endblock %}",
        }), "django.template.loaders.filesystem.Loader"],
        libraries={"static": "django.templatetags.static"},
    )
    return engine.get_template("turno_caja.html").render(Context({"close_page": page}))


class _ClosureMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.invoice_inputs = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "facturas_pagadas":
            self.invoice_inputs.append((attrs, list(self.stack)))
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img",
                       "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break


class PaidInvoicesMarkupTests(SimpleTestCase):
    def test_admin_displays_paid_invoices_as_a_read_only_metric(self):
        template = (settings.BASE_DIR / "mainApp/templates/turnos_caja_admin.html").read_text(
            encoding="utf-8",
        )
        self.assertIn("Facturas pagadas (caja)", template)
        self.assertIn('<div id="mFacturasPagadas" class="v">', template)
        self.assertIn("turnos_caja_admin.js' %}?v=6", template)

    def test_single_invoice_input_is_only_in_the_initial_payments_step(self):
        markup = _ClosureMarkup()
        markup.feed(render_close_page("payments"))
        self.assertEqual(len(markup.invoice_inputs), 1)
        attrs, ancestors = markup.invoice_inputs[0]
        ancestor_ids = {attrs.get("id") for _, attrs in ancestors}
        self.assertIn("stepClose", ancestor_ids)
        self.assertIn("closePaidInvoices", ancestor_ids)
        self.assertIn("closePaymentsStep", ancestor_ids)
        self.assertTrue({"closeCashStep", "closeMediaStep"}.isdisjoint(ancestor_ids))
        self.assertEqual(attrs["min"], "0")
        self.assertEqual(attrs["value"], "0")
        self.assertNotIn("disabled", attrs)
        self.assertNotIn("readonly", attrs)
        for _, ancestor in ancestors:
            if ancestor.get("id") != "stepClose":
                self.assertNotIn("hidden", ancestor)
                self.assertNotIn("display:none", ancestor.get("style", "").replace(" ", ""))

    def test_payments_precede_cash_and_cash_warning_precedes_denominations(self):
        template = (settings.BASE_DIR / "mainApp/templates/turno_caja.html").read_text(encoding="utf-8")
        self.assertLess(template.index('id="closePaymentsStep"'), template.index('id="closeCashStep"'))
        self.assertLess(template.index('id="closeCashStep"'), template.index('id="closeMediaStep"'))
        self.assertLess(template.index('id="closeCashWarningTitle"'), template.index('id="closeDenomInputs"'))
        self.assertIn("No cuentes el dinero que ya pagaste o que vas a pagarle a un compañero.", template)
        self.assertIn("incluida la base", template)
        self.assertIn("aparta primero el dinero", template)
        self.assertIn('id="btnPaymentsNext"', template)
        self.assertNotIn('id="btnBackPayments"', template)
        self.assertNotIn('id="btnBackCash"', template)
        self.assertIn("no podrás volver atrás ni cambiar esos valores", template)

    def test_cash_warning_has_large_legible_text(self):
        css = (settings.BASE_DIR / "mainApp/static/css/turno_caja.css").read_text(encoding="utf-8")
        self.assertIn(".tc-cash-warning", css)
        self.assertIn("font-size:clamp(20px,2.7vw,27px)", css)
        self.assertIn("background:#fff3cd", css)
        self.assertIn("color:#392700", css)

    def test_each_url_only_renders_its_own_step_even_without_css_or_javascript(self):
        from django.urls import resolve
        from .permissions import ROUTE_PERMISSIONS
        for page, slug, panel in (
            ("payments", "pagos", "closePaymentsStep"),
            ("cash", "efectivo", "closeCashStep"),
            ("media", "medios", "closeMediaStep"),
        ):
            with self.subTest(page=page):
                html = render_close_page(page)
                self.assertIn(f'id="{panel}"', html)
                for other in {"closePaymentsStep", "closeCashStep", "closeMediaStep"} - {panel}:
                    self.assertNotIn(f'id="{other}"', html)
                name = f"turno_caja_cierre_{slug}"
                route = resolve(reverse(name, kwargs={"turno_id": 123}))
                self.assertEqual(route.func.view_initkwargs["close_page"], page)
                self.assertEqual(ROUTE_PERMISSIONS[name], "caja_turno")
        self.assertNotIn('id="closeDenomInputs"', render_close_page("payments"))
        self.assertNotIn('id="closePaidInvoices"', render_close_page("cash"))


class PaidInvoicesClosureTests(TestCase):
    def setUp(self):
        self.cashier = Usuario.objects.create_user("Cajero prueba facturas")
        branch = Sucursal.objects.create(nombre="Sucursal prueba facturas")
        payment_point = PuntosPago.objects.create(nombre="Caja prueba", sucursalid=branch)
        self.turn = TurnoCaja.objects.create(
            puntopago=payment_point,
            cajero=self.cashier,
            estado="CIERRE",
            cierre_iniciado=timezone.now(),
            saldo_apertura_efectivo=Decimal("1000.00"),
        )

    def close_page(self, page="payments", user=None, turno_id=None):
        request = RequestFactory().get("/turno_caja/cierre/prueba/")
        request.user = user if user is not None else self.cashier
        with (
            patch("mainApp.views._hide_bd_cols_for_user", return_value=True),
            patch("mainApp.views._require_admin", return_value=False),
            patch("mainApp.views._turno_frontend_payload", return_value={
                "turno_id": self.turn.pk, "estado": self.turn.estado,
            }),
        ):
            return TurnoCajaCierrePageView.as_view(close_page=page)(
                request, turno_id=self.turn.pk if turno_id is None else turno_id,
            )

    def test_close_pages_require_login(self):
        response = self.close_page(user=AnonymousUser())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))

    def test_pending_close_redirects_from_main_page_without_javascript(self):
        request = RequestFactory().get(reverse("turno_caja"))
        request.user = self.cashier
        with patch("mainApp.views._turno_frontend_payload") as payload:
            response = TurnoCajaPageView.as_view()(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("turno_caja_cierre_pagos", kwargs={"turno_id": self.turn.pk}))
        self.assertEqual(response["Cache-Control"], "no-store, private")
        payload.assert_not_called()

    def test_open_turn_stays_on_main_page(self):
        self.turn.estado = "ABIERTO"
        self.turn.save(update_fields=["estado"])
        request = RequestFactory().get(reverse("turno_caja"))
        request.user = self.cashier
        with (
            patch("mainApp.views._hide_bd_cols_for_user", return_value=True),
            patch("mainApp.views._turno_frontend_payload", return_value={"turno_id": self.turn.pk, "estado": "ABIERTO"}),
        ):
            response = TurnoCajaPageView.as_view()(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context_data["close_page"], "")

    def test_close_pages_preserve_the_owned_turn_and_selected_page(self):
        for page in ("payments", "cash", "media"):
            with self.subTest(page=page):
                response = self.close_page(page)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context_data["close_page"], page)
                self.assertEqual(response.context_data["turno_activo_inicial"]["turno_id"], self.turn.pk)
                self.assertEqual(response["Cache-Control"], "no-store, private")

    def test_close_pages_reject_another_cashiers_turn(self):
        other = Usuario.objects.create_user("Otro cajero de prueba")
        with self.assertRaises(PermissionDenied):
            self.close_page(user=other)

    def test_close_pages_reject_unknown_turn(self):
        with self.assertRaises(Http404):
            self.close_page(turno_id=self.turn.pk + 1000)

    def test_close_pages_redirect_if_turn_is_not_in_closing(self):
        for state in ("ABIERTO", "CERRADO"):
            with self.subTest(state=state):
                self.turn.estado = state
                self.turn.save(update_fields=["estado"])
                response = self.close_page()
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, reverse("turno_caja"))

    def close_turn(self, paid_invoices=None):
        payload = {
            "turno_id": self.turn.pk,
            "efectivo_entregado": "9000.00",
            "medios_json": json.dumps([{"metodo": "tarjeta", "contado": "5000.00"}]),
        }
        if paid_invoices is not None:
            payload["facturas_pagadas"] = paid_invoices
        request = RequestFactory().post(reverse("turno_caja_cerrar"), payload)
        request.user = self.cashier
        with (
            patch("mainApp.views._expected_por_metodo", return_value=(
                {"efectivo": Decimal("10000"), "tarjeta": Decimal("5000")},
                Decimal("15000"), Decimal("10000"), Decimal("5000"),
            )),
            patch("mainApp.views.payment_method_options", return_value=[
                {"code": "efectivo"}, {"code": "tarjeta"},
            ]),
            patch("mainApp.views._auto_confirmados_por_metodo", return_value={}),
            patch("mainApp.views._manuales_sin_api_por_metodo", return_value={}),
            patch("mainApp.views._sum_reintegros_por_metodo", return_value={}),
        ):
            return TurnoCajaCerrarApi.as_view()(request)

    def test_paid_invoices_are_saved_and_reconcile_cash_exactly_once(self):
        response = self.close_turn("2000.00")
        self.assertEqual(response.status_code, 200, response.content)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "CERRADO")
        self.assertEqual(self.turn.medios.get(metodo="facturas_pagadas").contado, Decimal("2000"))
        self.assertEqual(self.turn.medios.get(metodo="efectivo").contado, Decimal("10000"))
        self.assertEqual(self.turn.ventas_total, Decimal("15000"))
        self.assertEqual(self.turn.efectivo_real, Decimal("9000"))
        self.assertEqual(self.turn.real_total, Decimal("14000"))
        self.assertEqual(self.turn.diferencia_total, Decimal("0"))
        self.assertEqual(self.turn.deuda_total, Decimal("0"))
        self.assertEqual(json.loads(response.content)["facturas_pagadas"], 2000.0)

    def test_paid_invoices_remain_optional(self):
        response = self.close_turn()
        self.assertEqual(response.status_code, 200, response.content)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.medios.get(metodo="facturas_pagadas").contado, Decimal("0"))
        self.assertEqual(self.turn.deuda_total, Decimal("-2000"))

    def test_negative_paid_invoices_do_not_close_the_turn(self):
        response = self.close_turn("-1")
        self.assertEqual(response.status_code, 400)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "CIERRE")
        self.assertFalse(self.turn.medios.exists())

    def admin_detail(self, allowed=True):
        request = RequestFactory().get(reverse("api_admin_turno_detail", args=[self.turn.pk]))
        request.user = self.cashier
        with patch("mainApp.views._can_edit_turnos", return_value=allowed):
            return TurnoCajaAdminDetailAPI.as_view()(request, turno_id=self.turn.pk)

    def test_admin_detail_reports_saved_invoices_separately_from_payment_methods(self):
        self.assertEqual(self.close_turn("2000.25").status_code, 200)
        response = self.admin_detail()
        self.assertEqual(response.status_code, 200, response.content)
        payload = json.loads(response.content)
        self.assertEqual(payload["turno"]["facturas_pagadas"], 2000.25)
        self.assertNotIn("facturas_pagadas", {m["metodo"] for m in payload["medios"]})
        self.assertEqual(payload["turno"]["ventas_total"], 15000.25)
        self.assertEqual(sum(m["contado"] for m in payload["medios"]), 15000.25)

    def test_admin_detail_defaults_to_zero_for_turns_without_invoice_record(self):
        response = self.admin_detail()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(json.loads(response.content)["turno"]["facturas_pagadas"], 0.0)
        self.assertFalse(self.turn.medios.exists())

    def test_admin_detail_keeps_permission_checks(self):
        response = self.admin_detail(allowed=False)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("turno", json.loads(response.content))

    def test_admin_save_preserves_invoices_without_counting_them_twice(self):
        self.assertEqual(self.close_turn("2000.00").status_code, 200)
        detail = json.loads(self.admin_detail().content)
        turn = detail["turno"]
        request = RequestFactory().post(
            reverse("api_admin_turno_update", args=[self.turn.pk]),
            data=json.dumps({
                "estado": turn["estado"],
                "saldo_apertura_efectivo": turn["saldo_apertura_efectivo"],
                "inicio_local": turn["inicio_local"],
                "cierre_iniciado_local": turn["cierre_iniciado_local"],
                "fin_local": turn["fin_local"],
                "efectivo_real": turn["efectivo_real"],
                "medios": detail["medios"],
            }),
            content_type="application/json",
        )
        request.user = self.cashier
        with patch("mainApp.views._can_edit_turnos", return_value=True):
            response = TurnoCajaAdminUpdateAPI.as_view()(request, turno_id=self.turn.pk)
        self.assertEqual(response.status_code, 200, response.content)
        fresh = json.loads(self.admin_detail().content)
        self.assertEqual(fresh["turno"]["facturas_pagadas"], 2000.0)
        self.assertEqual(fresh["turno"]["ventas_total"], 15000.0)
        self.assertEqual(fresh["turno"]["deuda_total"], 0.0)
