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

from mainApp.hybrid_models import EquipoHibrido, SesionHibrida
from mainApp.models import (
    AvanceCierreCaja, CambioDevolucion, Categoria, ConfiguracionFuncionalidad, DetalleVenta,
    Empleado, Inventario, MetodoPago, NotificacionNequi, PagoVenta, Producto,
    PuntosPago, ReintegroVenta, Sucursal, TurnoCaja, TurnoCajaMedio, Usuario, Venta,
)
from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE
from mainApp.views import VentaDetailView, _expected_por_metodo, _sum_reintegros_por_metodo


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

    def submit(self, refunds=None, *, quantity=1, journal=False, **changes):
        refunds = [] if refunds is None else refunds
        payload = {"accion": "guardar", "mediopago": self.sale.mediopago,
                   "dev-TOTAL_FORMS": "1", "dev-INITIAL_FORMS": "1",
                   "dev-0-detalle_id": self.detail.pk, "dev-0-devolver": str(quantity),
                   "pagos-TOTAL_FORMS": "0", "pagos-INITIAL_FORMS": "0",
                   "reint-TOTAL_FORMS": str(len(refunds)), "reint-INITIAL_FORMS": str(len(refunds))}
        for index, (method, amount) in enumerate(refunds):
            payload[f"reint-{index}-medio_pago"] = method
            payload[f"reint-{index}-monto"] = str(amount)
        payload.update(changes)
        request = self.factory.post(f"/ver_venta/{self.sale.pk}/", payload,
                                    **({"HTTP_X_NOVA_BUSINESS": "1"} if journal else {}))
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

    def turn_at(self, name, *, branch=None, state="ABIERTO"):
        point = PuntosPago.objects.create(nombre=name, sucursalid=branch or self.branch, dinerocaja=20000)
        turn = TurnoCaja.objects.create(cajero=self.user, puntopago=point, estado=state)
        if state == "CIERRE":
            turn.cierre_iniciado = timezone.now()
            turn.save(update_fields=["cierre_iniciado"])
        return turn

    def reserve_turn(self, turn):
        device = EquipoHibrido.objects.create(nombre="Equipo de prueba", punto=turn.puntopago,
            enlace_hash="test", enlace_vence=timezone.now(), creado_por=self.user)
        SesionHibrida.objects.create(equipo=device, usuario=self.user, turno=turn, vence_en=timezone.now())

    def assert_no_refund_changes(self):
        self.sale.refresh_from_db()
        self.stock.refresh_from_db()
        self.detail.refresh_from_db()
        self.point.refresh_from_db()
        self.assertEqual((self.sale.total, self.sale.mediopago, self.stock.cantidad, self.detail.cantidad, self.point.dinerocaja),
                         (1000, "nequi", 10, 2, 10000))
        self.assertFalse(ReintegroVenta.objects.exists())
        self.assertFalse(CambioDevolucion.objects.exists())
        self.assertEqual(PagoVenta.objects.get(ventaid=self.sale).medio_pago, "nequi")

    def test_selected_other_register_gets_the_whole_refund_and_origin_is_unchanged(self):
        original = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point, estado="ABIERTO")
        selected = self.turn_at("Caja elegida", state="CIERRE")
        AvanceCierreCaja.objects.create(turno=selected, registrado_por=self.user, progreso={"step": "cash"})
        response, request = self.submit([("efectivo", 200), ("nequi", 300)], turno_reintegro_id=selected.pk)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any(f"turno #{selected.pk}" in str(msg) for msg in get_messages(request)))
        self.assertEqual(set(ReintegroVenta.objects.values_list("turno_id", flat=True)), {selected.pk})
        self.assertEqual(_sum_reintegros_por_metodo(selected), {"efectivo": 200, "nequi": 300})
        self.assertEqual(_sum_reintegros_por_metodo(original), {})
        self.assertEqual(dict(selected.medios.values_list("metodo", "esperado")), {"efectivo": -200, "nequi": -300})
        expected, total, _, _ = _expected_por_metodo(selected)
        self.assertEqual((expected["efectivo"], expected["nequi"], total), (-200, -300, -500))
        self.assertFalse(original.medios.exists())
        self.sale.refresh_from_db()
        self.assertEqual((self.sale.puntopagoid_id, self.sale.total), (self.point.pk, 500))
        self.assertEqual(PagoVenta.objects.get(ventaid=self.sale).monto, 1000)
        self.assert_closed_unchanged()

    def test_picker_only_lists_eligible_shifts_in_the_sale_branch(self):
        original = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point)
        eligible = self.turn_at("Caja alternativa", state="CIERRE")
        self.turn_at("Caja ajena", branch=Sucursal.objects.create(nombre="Otra sucursal"))
        reserved = self.turn_at("Caja reservada")
        self.reserve_turn(reserved)
        counted = self.turn_at("Caja contada", state="CIERRE")
        AvanceCierreCaja.objects.create(turno=counted, registrado_por=self.user, progreso={"step": "media"})
        request = self.factory.get(f"/ver_venta/{self.sale.pk}/")
        request.user = self.user
        with patch("mainApp.views.render", return_value=HttpResponse()) as render_view:
            self.assertEqual(VentaDetailView().get(request, self.sale.pk).status_code, 200)
        picker = render_view.call_args.args[2]["refund_turn_picker"]
        self.assertEqual({turn.pk for turn in picker["turns"]}, {original.pk, eligible.pk})
        self.assertEqual(picker["default_id"], original.pk)
        self.assertFalse(picker["allow_no_turn"])

    def test_closed_foreign_reserved_or_counted_turn_is_not_charged_and_no_edits_are_saved(self):
        TurnoCaja.objects.create(cajero=self.user, puntopago=self.point)
        foreign = self.turn_at("Ajena", branch=Sucursal.objects.create(nombre="Otra"))
        reserved = self.turn_at("Reservada")
        self.reserve_turn(reserved)
        counted = self.turn_at("Contada", state="CIERRE")
        AvanceCierreCaja.objects.create(turno=counted, registrado_por=self.user, progreso={"step": "media"})
        for choice in (self.closed.pk, foreign.pk, reserved.pk, counted.pk, "900000000", "", "sin_turno", "9" * 100):
            with self.subTest(choice=choice):
                _, request = self.submit(turno_reintegro_id=choice, mediopago="efectivo")
                self.assertTrue(any(msg.level_tag == "error" for msg in get_messages(request)))
                self.assert_no_refund_changes()
        self.assert_closed_unchanged()

    def test_explicit_no_turn_does_not_charge_another_register(self):
        other = self.turn_at("Otra caja abierta")
        self.submit(turno_reintegro_id="sin_turno")
        self.assertIsNone(ReintegroVenta.objects.get().turno_id)
        self.assertFalse(other.medios.exists())
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, 9500)

    def test_invalid_selection_returns_a_conflict_to_the_installed_app(self):
        response, _ = self.submit(turno_reintegro_id=self.closed.pk, journal=True)
        self.assertEqual(response.status_code, 409)
        self.assert_no_refund_changes()

    def test_selection_from_old_page_is_rejected_if_turn_control_was_disabled(self):
        chosen = self.turn_at("Caja disponible antes")
        self.feature.habilitada = False
        self.feature.save(update_fields=["habilitada"])
        _, request = self.submit(turno_reintegro_id=chosen.pk)
        self.assertTrue(any("control de turnos cambió" in str(msg) for msg in get_messages(request)))
        self.assert_no_refund_changes()

    def test_selected_turn_is_ignored_when_saving_only_the_payment_method(self):
        original = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point)
        self.submit(quantity=0, mediopago="efectivo", turno_reintegro_id=self.closed.pk)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.mediopago, "efectivo")
        self.assertFalse(ReintegroVenta.objects.exists())
        self.assertEqual(original.medios.get(metodo="efectivo").esperado, 1000)
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
