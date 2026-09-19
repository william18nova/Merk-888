"""Devoluciones web sin turno: salida auditable, no altera cierres históricos."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.cache import cache
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.utils import timezone

from mainApp.models import (
    CambioDevolucion, Categoria, ConfiguracionFuncionalidad, DetalleVenta,
    Empleado, Inventario, MetodoPago, NotificacionNequi, PagoVenta, Producto,
    PuntosPago, ReintegroVenta, Sucursal, TurnoCaja, TurnoCajaMedio, Usuario, Venta,
)
from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE
from mainApp.views import VentaDetailView, _sum_reintegros_por_metodo


class RefundWithoutActiveTurnTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = Usuario.objects.create_user("Operador de pruebas")
        self.branch = Sucursal.objects.create(nombre="Prueba")
        self.point = PuntosPago.objects.create(nombre="Principal", sucursalid=self.branch, dinerocaja=10000)
        employee = Empleado(nombre="Prueba", apellido="Aislada", telefono="", email="",
                            numerodocumento="TEST", puesto="Cajero", sucursalid=self.branch)
        Empleado.objects.bulk_create([employee])
        category = Categoria.objects.create(nombre="Prueba")
        self.product = Producto.objects.create(nombre="Producto de prueba", categoria=category, precio=500)
        self.stock = Inventario.objects.create(productoid=self.product, sucursalid=self.branch, cantidad=10)
        self.sale = Venta.objects.create(fecha=timezone.localdate(), hora="12:00", empleadoid=employee,
                                        sucursalid=self.branch, puntopagoid=self.point, mediopago="nequi", total=1000)
        self.detail = DetalleVenta.objects.create(ventaid=self.sale, productoid=self.product,
                                                cantidad=2, preciounitario=500)
        PagoVenta.objects.create(ventaid=self.sale, medio_pago="nequi", monto=1000)
        self.notification = NotificacionNequi.objects.create(venta=self.sale, texto="Ingreso de prueba",
                                                            monto=1000, es_ingreso=True, fingerprint="refund-test")
        self.closed = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point, estado="CERRADO",
                                              esperado_total=1000, ventas_total=1000)
        TurnoCajaMedio.objects.create(turno=self.closed, metodo="nequi", esperado=1000, contado=1000)
        self.closed_before = TurnoCaja.objects.filter(pk=self.closed.pk).values().get()
        for code in ("efectivo", "nequi", "tarjeta"):
            MetodoPago.objects.create(codigo=code, nombre=code, activo=True, es_efectivo=code == "efectivo")
        self.feature = ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=True)
        # La autorización se prueba por separado: estas pruebas ejercitan la
        # vista de devolución con un operador autorizado, no suplantan usuarios reales.
        for name, value in (("_can_print_venta", True), ("_is_print_only", False)):
            patcher = patch.object(VentaDetailView, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.factory = RequestFactory()

    def submit(self, refunds=None, *, quantity=1):
        refunds = [] if refunds is None else refunds
        payload = {"accion": "guardar", "mediopago": self.sale.mediopago,
                   "dev-TOTAL_FORMS": "1", "dev-INITIAL_FORMS": "1",
                   "dev-0-detalle_id": self.detail.pk, "dev-0-devolver": str(quantity),
                   "pagos-TOTAL_FORMS": "0", "pagos-INITIAL_FORMS": "0",
                   "reint-TOTAL_FORMS": str(len(refunds)), "reint-INITIAL_FORMS": str(len(refunds))}
        for index, (method, amount) in enumerate(refunds):
            payload[f"reint-{index}-medio_pago"] = method
            payload[f"reint-{index}-monto"] = str(amount)
        request = self.factory.post(f"/ver_venta/{self.sale.pk}/", payload)
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)
        response = VentaDetailView().post(request, self.sale.pk)
        return response, request

    def assert_closed_unchanged(self):
        self.assertEqual(TurnoCaja.objects.filter(pk=self.closed.pk).values().get(), self.closed_before)
        medium = TurnoCajaMedio.objects.get(turno=self.closed)
        self.assertEqual((medium.esperado, medium.contado), (1000, 1000))

    def test_cash_refund_without_turn_preserves_original_nequi_payment_and_audit(self):
        response, request = self.submit()
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("Devolución registrada" in str(message) for message in get_messages(request)))
        refund = ReintegroVenta.objects.get()
        self.assertEqual((refund.medio_pago, refund.monto, refund.turno_id, refund.registrado_por_id),
                         ("efectivo", 500, None, self.user.pk))
        self.assertIsNotNone(refund.creado_en)
        self.point.refresh_from_db()
        self.sale.refresh_from_db()
        self.stock.refresh_from_db()
        self.detail.refresh_from_db()
        self.notification.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, 9500)
        self.assertEqual(self.sale.total, 500)
        self.assertEqual(self.detail.cantidad, 1)
        self.assertEqual(self.stock.cantidad, 11)
        self.assertEqual(PagoVenta.objects.get(ventaid=self.sale).monto, 1000)
        self.assertEqual(self.notification.venta_id, self.sale.pk)
        self.assertEqual(self.notification.monto, 1000)
        self.assert_closed_unchanged()
        future = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point, estado="ABIERTO")
        self.assertEqual(_sum_reintegros_por_metodo(future), {})

    def test_non_cash_refunds_without_turn_do_not_reduce_cash(self):
        for method in ("nequi", "tarjeta"):
            self.submit([(method, 500)])
        self.assertEqual(set(ReintegroVenta.objects.values_list("medio_pago", flat=True)), {"nequi", "tarjeta"})
        self.assertFalse(ReintegroVenta.objects.filter(turno__isnull=False).exists())
        self.point.refresh_from_db()
        self.sale.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, 10000)
        self.assertEqual(self.sale.total, 0)
        self.assertEqual(PagoVenta.objects.get(ventaid=self.sale).monto, 1000)
        self.assert_closed_unchanged()

    def test_split_refund_debits_only_cash_component(self):
        self.submit([("efectivo", 200), ("nequi", 300)])
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, 9800)
        self.assertEqual(ReintegroVenta.objects.count(), 2)
        self.assertFalse(ReintegroVenta.objects.filter(turno__isnull=False).exists())

    def test_existing_turn_keeps_refund_in_current_cashiers_closing(self):
        cashier = Usuario.objects.create_user("Otro cajero")
        current = TurnoCaja.objects.create(cajero=cashier, puntopago=self.point, estado="ABIERTO")
        self.submit()
        self.assertEqual(ReintegroVenta.objects.get().turno_id, current.pk)
        self.assertEqual(TurnoCajaMedio.objects.get(turno=current, metodo="efectivo").esperado, -500)
        self.assertEqual(_sum_reintegros_por_metodo(current), {"efectivo": Decimal("500.00")})
        self.assert_closed_unchanged()

    def test_cannot_return_more_than_remaining_without_turn(self):
        self.submit(quantity=2)
        response, request = self.submit(quantity=1)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any("No puedes devolver" in str(message) for message in get_messages(request)))
        self.assertEqual(ReintegroVenta.objects.count(), 1)
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, 9000)

    def test_invalid_refund_still_reports_error_on_sale_page(self):
        _, request = self.submit([("efectivo", 900)])
        messages = list(get_messages(request))
        self.assertTrue(any("debe ser igual" in str(message) for message in messages))
        def render_page(req, template, context):
            context.update(messages=messages, request=SimpleNamespace(resolver_match=SimpleNamespace(url_name="login")))
            return HttpResponse(render_to_string(template, context))
        with patch("mainApp.views.render", side_effect=render_page):
            response = VentaDetailView().get(request, self.sale.pk)
        self.assertContains(response, str(messages[0]))
        self.assertContains(response, 'role="alert"')
        self.assertFalse(ReintegroVenta.objects.exists())
        self.assertFalse(CambioDevolucion.objects.exists())
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, 10000)

    def test_removing_turn_requirement_does_not_grant_return_permission(self):
        with patch.object(VentaDetailView, "_is_print_only", return_value=True):
            response, _ = self.submit()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ReintegroVenta.objects.exists())
        self.assertFalse(CambioDevolucion.objects.exists())
