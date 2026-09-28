"""Pruebas del recibo inmediato, sin base externa ni impresora real."""

from contextlib import nullcontext
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from .models import PuntosPago, Sucursal, Venta
from .services.printing import resolve_print_profile
from .views import GenerarVentaView, _load_sale_print_token


@override_settings(SECRET_KEY="cash-receipt-tests-only")
class CashReceiptTests(SimpleTestCase):
    def _create_sale_receipt(self, *, received, payments, system="windows", size="grande"):
        branch = Sucursal(pk=7, nombre="Sucursal de prueba")
        point = PuntosPago(pk=19)
        user = SimpleNamespace(
            pk=91,
            empleado=SimpleNamespace(nombre="Cajero", apellido="Prueba"),
        )
        sale = Venta(pk=123, puntopagoid=point)
        profile = resolve_print_profile(system, size)
        details = [{
            "productoid": 5,
            "producto": "Producto de prueba",
            "cantidad": 1,
            "precio_unitario": Decimal("7500"),
            "subtotal": Decimal("7500"),
        }]
        with (
            patch("mainApp.views.transaction.atomic", return_value=nullcontext()),
            patch("mainApp.views.payment_method_table_ready", return_value=False),
            patch("mainApp.views.locked_feature_enabled", return_value=False),
            patch("mainApp.views.Venta.objects.create", return_value=sale),
            patch("mainApp.views.DetalleVenta.objects.bulk_create"),
            patch("mainApp.views.Inventario.objects.filter") as inventory,
            patch("mainApp.views.PagoVenta.objects.bulk_create") as save_payments,
            patch("mainApp.views.PuntosPago.objects.filter") as cash,
            patch("mainApp.views.get_print_profile", return_value=profile),
            patch("mainApp.views.payment_method_label_map", return_value={
                "efectivo": "Efectivo", "nequi": "Nequi",
            }),
        ):
            inventory.return_value.values_list.return_value = [5]
            response = GenerarVentaView._crear_venta_ultra_fast(
                user, branch, point, None, payments, details,
                Decimal("7500"), Decimal(received), turno_requerido=False,
            )
        payload = json.loads(response.content)
        self.assertTrue(payload["success"], payload.get("error"))
        self.assertEqual(payload["sale_total"], "7500")
        if system == "linux":
            signed = _load_sale_print_token(payload["print_token"], sale, user, profile)
            self.assertEqual(signed["receipt_text"], payload["receipt_text"])
        else:
            self.assertEqual(payload["print_token"], "")
        return payload["receipt_text"], save_payments, cash

    def test_cash_receipt_includes_received_and_change_in_all_four_profiles(self):
        for system in ("windows", "linux"):
            for size, width in (("grande", 48), ("pequena", 32)):
                with self.subTest(system=system, size=size):
                    receipt, payments, cash = self._create_sale_receipt(
                        received="10000",
                        payments=[{"medio_pago": "efectivo", "monto": "7500.00"}],
                        system=system, size=size,
                    )
                    self.assertRegex(receipt, r"TOTAL:\s+\$7\.500\n")
                    self.assertRegex(receipt, r"RECIBIDO:\s+\$10\.000\n")
                    self.assertRegex(receipt, r"CAMBIO:\s+\$2\.500\n")
                    self.assertLessEqual(max(map(len, receipt.splitlines())), width)
                    # El recibido no debe inflar lo vendido ni el saldo de caja.
                    self.assertEqual(payments.call_args.args[0][0].monto, Decimal("7500"))
                    delta = cash.return_value.update.call_args.kwargs["dinerocaja"]
                    self.assertEqual(delta.rhs.value, Decimal("7500"))

    def test_exact_cash_or_empty_received_does_not_invent_change(self):
        for received in ("7500", "0"):
            with self.subTest(received=received):
                receipt, _, _ = self._create_sale_receipt(
                    received=received,
                    payments=[{"medio_pago": "efectivo", "monto": "7500.00"}],
                )
                self.assertRegex(receipt, r"RECIBIDO:\s+\$7\.500\n")
                self.assertNotIn("CAMBIO:", receipt)

    def test_non_cash_and_mixed_payments_ignore_stale_received_cash(self):
        for payments in (
            [{"medio_pago": "nequi", "monto": "7500.00"}],
            [
                {"medio_pago": "efectivo", "monto": "2500.00"},
                {"medio_pago": "nequi", "monto": "5000.00"},
            ],
        ):
            with self.subTest(payments=payments):
                receipt, _, _ = self._create_sale_receipt(received="10000", payments=payments)
                self.assertNotIn("RECIBIDO:", receipt)
                self.assertNotIn("CAMBIO:", receipt)
