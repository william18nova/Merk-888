from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    AvanceCierreCaja, CambioEgreso, ConceptoEgreso, ConfiguracionFuncionalidad,
    DesglosePagosCierre, Egreso, MetodoPago, PagoVenta, ReintegroVenta,
    TurnoCaja, TurnoCajaMedio, Venta,
)
from .services.close_progress import DENOMINATIONS, attempt_id
from .services.feature_flags import TURN_REQUIRED_FEATURE, clear_feature_cache
from .test_pos_request_receipts import ReceiptFixture
from .views import CO_TZ


class AdminTurnStateTests(ReceiptFixture, TestCase):
    def setUp(self):
        super().setUp()
        ConfiguracionFuncionalidad.objects.filter(clave=TURN_REQUIRED_FEATURE).update(habilitada=True)
        clear_feature_cache()
        self.addCleanup(clear_feature_cache)
        self.client.force_login(self.user)
        self.now = timezone.now().replace(microsecond=0)
        self.start = self.now - timedelta(hours=3)
        self.turn = TurnoCaja.objects.create(puntopago=self.point, cajero=self.user, saldo_apertura_efectivo=1000)
        TurnoCaja.objects.filter(pk=self.turn.pk).update(inicio=self.start)
        self.turn.refresh_from_db()
        self.update_url = reverse("api_admin_turno_update", args=[self.turn.pk])
        self.checkpoint_url = reverse("turno_caja_cierre_respaldo", args=[self.turn.pk])
        self.previous_counts = {key: int(key == "b5000") for key in DENOMINATIONS}
        self.previous_progress = {
            "step": "media", "invoices": "500.00", "denominations": self.previous_counts,
            "payment_details": [{"nombre": "PROVEEDOR ANTERIOR", "monto": "500.00"}],
        }
        self.sale_totals = {}
        if connection.vendor == "sqlite":
            # La consulta de producción usa fecha + hora, aritmética de PostgreSQL.
            query = patch("mainApp.views._sum_pagos_por_metodo", side_effect=lambda *_: self.sale_totals)
            query.start()
            self.addCleanup(query.stop)

    def change(self, state, **extra):
        return self.client.post(self.update_url, {"estado": state, **extra}, content_type="application/json")

    def begin(self):
        return self.client.post(reverse("turno_caja_iniciar_cierre"), {"turno_id": self.turn.pk})

    def checkpoint(self, **payload):
        return self.client.post(self.checkpoint_url, payload, content_type="application/json")

    def sale_record(self, method="efectivo", total=8000):
        stamp = timezone.localtime(self.start + timedelta(minutes=15), CO_TZ)
        sale = Venta.objects.create(fecha=stamp.date(), hora=stamp.time(), empleadoid=self.employee,
            sucursalid=self.branch, puntopagoid=self.point, total=total, mediopago=method)
        PagoVenta.objects.create(ventaid=sale, medio_pago=method, monto=total)
        self.sale_totals[method] = self.sale_totals.get(method, Decimal("0")) + Decimal(total)
        return sale

    def prior_close(self, state="CERRADO"):
        TurnoCaja.objects.filter(pk=self.turn.pk).update(estado=state,
            cierre_iniciado=self.now - timedelta(hours=1), fin=self.now - timedelta(minutes=55) if state == "CERRADO" else None,
            esperado_total=8000, efectivo_real=5000, real_total=5000, ventas_total=4500,
            ventas_efectivo=4500, diferencia_efectivo=-3500, diferencia_total=-3500, deuda_total=-3500)
        TurnoCajaMedio.objects.create(turno=self.turn, metodo="efectivo", esperado=8000, contado=4500, diferencia=-3500)
        AvanceCierreCaja.objects.create(turno=self.turn, registrado_por=self.user, progreso=self.previous_progress)
        if state == "CERRADO":
            TurnoCajaMedio.objects.create(turno=self.turn, metodo="facturas_pagadas", contado=500)
            DesglosePagosCierre.objects.create(turno=self.turn, registrado_por=self.user, total=500,
                pagos=self.previous_progress["payment_details"])

    def expense(self):
        return Egreso.objects.create(concepto=ConceptoEgreso.objects.create(nombre="Proveedor anterior"),
            monto=500, medio_pago="efectivo", registrado_por=self.user, registrado_por_nombre=self.user.get_username())

    def test_closed_to_open_clears_only_the_closing_and_recomputes_from_real_movements(self):
        sale = self.sale_record()
        ReintegroVenta.objects.create(venta=sale, turno=self.turn, medio_pago="efectivo", monto=1000)
        expense = self.expense()
        self.prior_close()
        response = self.change("ABIERTO", cierre_iniciado_local="2020-01-01T00:00", fin_local="2020-01-02T00:00",
            efectivo_real="5000", medios=[{"metodo": "efectivo", "esperado": "8000", "contado": "4500"}])
        self.assertEqual(response.status_code, 200, response.content)
        self.turn.refresh_from_db()
        self.assertEqual((self.turn.estado, self.turn.inicio, self.turn.saldo_apertura_efectivo), ("ABIERTO", self.start, 1000))
        for field in ("cierre_iniciado", "fin", "real_total", "efectivo_real"):
            self.assertIsNone(getattr(self.turn, field))
        for field in ("esperado_total", "ventas_total", "diferencia_total", "diferencia_efectivo", "deuda_total"):
            self.assertEqual(getattr(self.turn, field), 0)
        self.assertFalse(self.turn.medios.exclude(contado=None).exists())
        self.assertFalse(DesglosePagosCierre.objects.filter(turno=self.turn).exists())
        self.assertEqual(self.client.get(self.checkpoint_url).status_code, 409)
        self.assertTrue(PagoVenta.objects.filter(ventaid=sale, monto=8000).exists())
        self.assertTrue(ReintegroVenta.objects.filter(turno=self.turn, monto=1000).exists())
        self.assertTrue(Egreso.objects.filter(pk=expense.pk, monto=500).exists())

        begin = self.begin()
        self.assertEqual(begin.status_code, 200, begin.content)
        self.assertEqual(begin.json()["esperado_total"], 7000)
        self.assertTrue(begin.json()["cierre_intento"])
        self.assertTrue(all(m["contado"] is None for m in begin.json()["medios"]))
        self.assertIsNone(self.client.get(self.checkpoint_url).json()["progress"])
        self.assertEqual(self.checkpoint(**self.previous_progress).status_code, 409)

    def test_repeated_transitions_invalidate_old_tabs_but_do_not_reset_on_same_state_save(self):
        self.prior_close("CIERRE")
        self.assertEqual(self.change("CIERRE").status_code, 200)
        self.assertEqual(self.client.get(self.checkpoint_url).json()["progress"], self.previous_progress)
        self.assertEqual(self.change("ABIERTO").status_code, 200)
        first = self.begin().json()["cierre_intento"]
        self.assertEqual(self.checkpoint(**self.previous_progress, attempt=first).status_code, 200)
        self.assertEqual(self.begin().json()["cierre_intento"], first)
        self.assertEqual(self.change("ABIERTO").status_code, 200)
        self.assertEqual(self.change("CIERRE").status_code, 200)
        second = self.client.get(self.checkpoint_url).json()
        self.assertNotEqual(second["attempt"], first)
        self.assertIsNone(second["progress"])
        self.assertEqual(self.checkpoint(**self.previous_progress, attempt=first).status_code, 409)
        self.assertEqual(self.change("ABIERTO", estado_original="CIERRE", cierre_intento_original=first).status_code, 409)
        result = self.client.post(reverse("turno_caja_cerrar"), {
            "turno_id": self.turn.pk, "cierre_intento": first, "efectivo_entregado": "5000", "facturas_pagadas": "0",
        })
        self.assertEqual(result.status_code, 409, result.content)
        self.assertEqual(self.checkpoint(step="cash", invoices="0", payment_details=[], attempt=second["attempt"]).status_code, 200)
        current = self.client.post(reverse("turno_caja_cerrar"), {
            "turno_id": self.turn.pk, "cierre_intento": second["attempt"], "efectivo_entregado": "1000", "facturas_pagadas": "0",
        })
        self.assertEqual(current.status_code, 200, current.content)
        self.assertEqual(DesglosePagosCierre.objects.get(turno=self.turn).pagos, [])

    def test_direct_change_from_closed_to_closing_has_a_new_cutoff_and_empty_progress(self):
        self.prior_close()
        response = self.change("CIERRE", cierre_iniciado_local="2020-01-01T00:00", efectivo_real=5000)
        self.assertEqual(response.status_code, 200, response.content)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "CIERRE")
        self.assertGreaterEqual(self.turn.cierre_iniciado, self.now)
        self.assertIsNone(self.turn.fin)
        self.assertIsNone(self.turn.efectivo_real)
        self.assertIsNone(self.client.get(self.checkpoint_url).json()["progress"])
        self.assertFalse(self.turn.medios.exclude(contado=None).exists())

    def test_manual_close_requires_real_counts_and_calculates_expected_from_sales(self):
        MetodoPago.objects.create(codigo="nequi", nombre="Nequi", activo=True)
        self.sale_record()
        self.sale_record("nequi", 3000)
        self.assertEqual(self.change("CERRADO").status_code, 400)
        self.assertEqual(self.change("CERRADO", efectivo_real=9000).status_code, 400)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "ABIERTO")
        self.assertIsNone(self.turn.cierre_iniciado)
        self.assertFalse(self.turn.medios.exists())
        response = self.change("CERRADO", efectivo_real=9000, medios=[{"metodo": "nequi", "contado": "2900", "esperado": "5"}])
        self.assertEqual(response.status_code, 200, response.content)
        self.turn.refresh_from_db()
        self.assertEqual((self.turn.estado, self.turn.esperado_total, self.turn.deuda_total), ("CERRADO", 11000, -100))
        self.assertEqual(self.turn.real_total, Decimal("11900.00"))
        self.assertLessEqual(self.turn.inicio, self.turn.cierre_iniciado)
        self.assertLessEqual(self.turn.cierre_iniciado, self.turn.fin)
        self.assertFalse(Egreso.objects.exists())

    def test_manual_close_does_not_discard_confirmed_steps(self):
        self.prior_close("CIERRE")
        result = self.change("CERRADO", efectivo_real=5000)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(self.client.get(self.checkpoint_url).json()["progress"], self.previous_progress)

    def test_saving_open_repairs_legacy_closing_values_without_reusing_them(self):
        TurnoCajaMedio.objects.create(turno=self.turn, metodo="facturas_pagadas", contado=500)
        self.assertEqual(self.change("CERRADO", efectivo_real=1000).status_code, 409)
        self.assertEqual(self.change("ABIERTO").status_code, 200)
        self.assertIsNone(self.turn.medios.get(metodo="facturas_pagadas").contado)
        self.assertTrue(self.begin().json()["cierre_intento"])
        self.assertIsNone(self.client.get(self.checkpoint_url).json()["progress"])

    def test_does_not_reopen_over_another_turn_or_erase_an_audited_declaration(self):
        self.prior_close()
        later = TurnoCaja.objects.create(puntopago=self.point, cajero=self.user, estado="CERRADO")
        self.assertEqual(self.change("ABIERTO").status_code, 409)
        later.delete()
        expense = self.expense()
        CambioEgreso.objects.create(egreso=expense, usuario=self.user, usuario_nombre=self.user.get_username(),
            motivo=f"CONCILIACION WEB CIERRE {self.turn.pk}/0 | Alta desde declaración del cierre")
        for state in ("ABIERTO", "CIERRE"):
            self.assertEqual(self.change(state).status_code, 409)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, "CERRADO")
        self.assertTrue(DesglosePagosCierre.objects.filter(turno=self.turn, total=500).exists())
        self.assertTrue(Egreso.objects.filter(pk=expense.pk).exists())

    def test_closing_in_progress_without_confirmations_can_be_closed_manually(self):
        self.assertEqual(self.change("CIERRE").status_code, 200)
        self.turn.refresh_from_db()
        cutoff = self.turn.cierre_iniciado
        previous_attempt = attempt_id(self.turn)
        response = self.change("CERRADO", efectivo_real=1000)
        self.assertEqual(response.status_code, 200, response.content)
        self.turn.refresh_from_db()
        self.assertEqual((self.turn.estado, self.turn.cierre_iniciado, self.turn.deuda_total), ("CERRADO", cutoff, 0))
        self.assertNotEqual(attempt_id(self.turn), previous_attempt)

    def test_edits_can_preserve_legacy_dates_but_cannot_introduce_a_new_invalid_order(self):
        self.prior_close()
        old_cutoff = self.start - timedelta(seconds=1)
        TurnoCaja.objects.filter(pk=self.turn.pk).update(cierre_iniciado=old_cutoff)
        detail = self.client.get(reverse("api_admin_turno_detail", args=[self.turn.pk])).json()["turno"]
        unchanged = {key: detail[key] for key in ("inicio_local", "cierre_iniciado_local", "fin_local")}
        response = self.change("CERRADO", **unchanged, efectivo_real=5100)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.change("CERRADO", fin_local="2020-01-01T00:00").status_code, 400)
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.cierre_iniciado, old_cutoff)
        self.assertEqual(self.turn.efectivo_real, Decimal("5100.00"))
