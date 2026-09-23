import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import RequestFactory, TestCase
from django.utils import timezone

from .models import Empleado, NotificacionNequi, PuntosPago, Sucursal, Venta
from .views import NequiNotificacionesDisponiblesView


class NequiSelectedPaymentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        NotificacionNequi.objects.bulk_create([
            NotificacionNequi(
                notificacionid=i, texto="Pago de prueba", monto=Decimal("5000"),
                es_ingreso=True, fingerprint=f"test-selected-{i}",
                recibido_en=now + timedelta(seconds=i),
            ) for i in range(1, 122)
        ])

    def get_items(self, selected_id=None):
        params = {} if selected_id is None else {"selected_id": selected_id}
        request = RequestFactory().get("/nequi/disponibles/", params)
        with patch("mainApp.views.is_feature_enabled", return_value=True):
            response = NequiNotificacionesDisponiblesView().get(request)
        return json.loads(response.content)["items"]

    def test_default_list_remains_limited_to_120_recent_payments(self):
        with self.assertNumQueries(1):
            items = self.get_items()
        self.assertEqual(len(items), 120)
        self.assertNotIn(1, [row["id"] for row in items])

    def test_selected_payment_outside_recent_window_is_included_without_linking(self):
        with self.assertNumQueries(2):
            items = self.get_items(1)
        self.assertEqual(len(items), 121)
        self.assertEqual(items[-1]["id"], 1)
        self.assertIsNone(NotificacionNequi.objects.get(pk=1).venta_id)
        self.assertIsNone(NotificacionNequi.objects.get(pk=1).usado_en)

    def test_selected_payment_already_in_list_is_not_duplicated(self):
        with self.assertNumQueries(1):
            items = self.get_items(121)
        self.assertEqual(len(items), 120)
        self.assertEqual(sum(row["id"] == 121 for row in items), 1)

    def test_selected_outgoing_or_invalid_amount_is_not_exposed(self):
        for changes in ({"es_ingreso": False}, {"es_ingreso": True, "monto": 0}, {"monto": None}):
            with self.subTest(changes=changes):
                NotificacionNequi.objects.filter(pk=1).update(**changes)
                self.assertNotIn(1, [row["id"] for row in self.get_items(1)])

    def test_invalid_selected_ids_do_not_fail_or_trigger_extra_queries(self):
        for selected_id in ("abc", "-1", "0", "1 OR 1=1", "9" * 100, "9223372036854775808"):
            with self.subTest(selected_id=selected_id), self.assertNumQueries(1):
                self.assertEqual(len(self.get_items(selected_id)), 120)

    def test_already_linked_payment_is_never_returned_even_when_explicitly_requested(self):
        branch = Sucursal.objects.create(nombre="Sucursal pruebas Nequi")
        point = PuntosPago.objects.create(nombre="Caja pruebas", sucursalid=branch)
        employee = Empleado(nombre="Prueba", apellido="Nequi", telefono="", email="",
                            numerodocumento="TEST-NEQUI", puesto="Cajero", sucursalid=branch)
        Empleado.objects.bulk_create([employee])
        sale = Venta.objects.create(fecha=timezone.localdate(), hora="12:00", empleadoid=employee,
                                    sucursalid=branch, puntopagoid=point, mediopago="nequi", total=1000)
        NotificacionNequi.objects.filter(pk=1).update(venta=sale, usado_en=timezone.now())
        self.assertNotIn(1, [row["id"] for row in self.get_items(1)])
        self.assertEqual(NotificacionNequi.objects.get(pk=1).venta_id, sale.pk)
