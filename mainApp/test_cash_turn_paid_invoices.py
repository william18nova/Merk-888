"""Facturas pagadas: visibles durante todo el cierre y sin duplicar el cuadre."""

import json
from decimal import Decimal
from html.parser import HTMLParser
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import PuntosPago, Sucursal, TurnoCaja, Usuario
from .views import TurnoCajaAdminDetailAPI, TurnoCajaAdminUpdateAPI, TurnoCajaCerrarApi


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

    def test_single_input_is_visible_in_both_closure_steps(self):
        markup = _ClosureMarkup()
        markup.feed((settings.BASE_DIR / "mainApp/templates/turno_caja.html").read_text(
            encoding="utf-8",
        ))
        self.assertEqual(len(markup.invoice_inputs), 1)
        attrs, ancestors = markup.invoice_inputs[0]
        ancestor_ids = {attrs.get("id") for _, attrs in ancestors}
        self.assertIn("stepClose", ancestor_ids)
        self.assertIn("closePaidInvoices", ancestor_ids)
        self.assertTrue({"closeCashStep", "closeMediaStep"}.isdisjoint(ancestor_ids))
        self.assertEqual(attrs["form"], "formClose")
        self.assertEqual(attrs["min"], "0")
        self.assertNotIn("disabled", attrs)
        self.assertNotIn("readonly", attrs)
        for _, ancestor in ancestors:
            if ancestor.get("id") != "stepClose":
                self.assertNotIn("hidden", ancestor)
                self.assertNotIn("display:none", ancestor.get("style", "").replace(" ", ""))


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
