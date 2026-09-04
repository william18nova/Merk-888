import json
from decimal import Decimal

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (
    ConceptoEgreso,
    ConfiguracionFuncionalidad,
    Egreso,
    Empleado,
    MetodoPago,
    PagoVenta,
    PuntosPago,
    Rol,
    Sucursal,
    TurnoCaja,
    Usuario,
    Venta,
)
from .permissions import ALWAYS_ALLOWED_URL_NAMES, NAV_GROUPS
from .services.feature_flags import TURN_REQUIRED_FEATURE, clear_feature_cache
from .views import MetricasNegocioDataView, _expected_por_metodo


class OperationalExpenseTests(TestCase):
    def setUp(self):
        self.role = Rol.objects.create(nombre="Cajero")
        self.user = Usuario.objects.create_user(
            nombreusuario="cajero-egresos",
            password="prueba-segura",
            rolid=self.role,
        )
        self.branch = Sucursal.objects.create(nombre="Sucursal egresos")
        self.point = PuntosPago.objects.create(
            sucursalid=self.branch,
            nombre="Caja egresos",
            dinerocaja=Decimal("5000.00"),
        )
        self.employee = Empleado.objects.create(
            nombre="Caja",
            apellido="Pruebas",
            telefono="3001002001",
            email="caja-egresos@example.com",
            direccion="Pruebas",
            puesto="Cajero",
            numerodocumento="DOC-EGRESOS-1",
            usuarioid=self.user,
            sucursalid=self.branch,
        )
        MetodoPago.objects.create(
            codigo="efectivo",
            nombre="Efectivo",
            activo=True,
            es_efectivo=True,
            es_sistema=True,
            orden=10,
        )
        MetodoPago.objects.create(
            codigo="nequi",
            nombre="Nequi",
            activo=True,
            es_sistema=True,
            orden=20,
        )

    def tearDown(self):
        clear_feature_cache(TURN_REQUIRED_FEATURE)

    def set_turn_required(self, enabled):
        ConfiguracionFuncionalidad.objects.update_or_create(
            clave=TURN_REQUIRED_FEATURE,
            defaults={"habilitada": enabled},
        )
        clear_feature_cache(TURN_REQUIRED_FEATURE)

    def test_page_is_available_to_every_authenticated_role_and_in_nav(self):
        self.assertIn("registrar_egreso", ALWAYS_ALLOWED_URL_NAMES)
        caja = next(group for group in NAV_GROUPS if group["label"] == "Caja")
        self.assertIn(
            "registrar_egreso",
            {item["url_name"] for item in caja["children"]},
        )

        self.client.force_login(self.user)
        response = self.client.get(reverse("registrar_egreso"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar pago")
        self.assertContains(response, "no modifica ningún turno ni caja")
        self.assertNotContains(response, "Punto de pago")

    def test_new_concept_is_normalized_without_changing_cash(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("registrar_egreso"), {
            "concepto": "  servicio   de energía  ",
            "monto": "1250.50",
            "medio_pago": "efectivo",
        })

        self.assertRedirects(response, reverse("registrar_egreso"))
        expense = Egreso.objects.select_related("concepto").get()
        self.assertEqual(expense.concepto.nombre, "SERVICIO DE ENERGÍA")
        self.assertEqual(expense.monto, Decimal("1250.50"))
        self.assertEqual(expense.registrado_por, self.user)
        self.assertEqual(expense.registrado_por_nombre, "cajero-egresos")
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("5000.00"))

    def test_existing_normalized_concept_is_reused(self):
        existing = ConceptoEgreso.objects.create(
            nombre="SERVICIO DE AGUA",
            creado_por=self.user,
        )
        self.client.force_login(self.user)

        for raw_name in ("servicio de agua", " SERVICIO   DE AGUA "):
            response = self.client.post(reverse("registrar_egreso"), {
                "concepto": raw_name,
                "monto": "100",
                "medio_pago": "nequi",
            })
            self.assertEqual(response.status_code, 302)

        self.assertEqual(ConceptoEgreso.objects.count(), 1)
        self.assertEqual(Egreso.objects.filter(concepto=existing).count(), 2)

    def test_payment_is_independent_from_turns_and_cash_closure(self):
        self.set_turn_required(True)
        turn = TurnoCaja.objects.create(
            puntopago=self.point,
            cajero=self.user,
            saldo_apertura_efectivo=Decimal("0.00"),
            estado="ABIERTO",
        )
        self.client.force_login(self.user)

        response = self.client.post(reverse("registrar_egreso"), {
            "concepto": "compra de bolsas",
            "monto": "500",
            "medio_pago": "nequi",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Egreso.objects.get().monto, Decimal("500.00"))
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("5000.00"))

        turn.estado = "CIERRE"
        turn.cierre_iniciado = timezone.now()
        turn.save(update_fields=["estado", "cierre_iniciado"])
        expected, total, cash, non_cash = _expected_por_metodo(turn)
        self.assertEqual(expected["nequi"], Decimal("0.00"))
        self.assertEqual(total, Decimal("0.00"))
        self.assertEqual(cash, Decimal("0.00"))
        self.assertEqual(non_cash, Decimal("0.00"))

    def test_metrics_show_sales_expenses_and_remaining_by_method(self):
        sale = Venta.objects.create(
            fecha=timezone.localdate(),
            hora=timezone.localtime().time(),
            empleadoid=self.employee,
            sucursalid=self.branch,
            puntopagoid=self.point,
            total=Decimal("1000.00"),
            mediopago="efectivo",
        )
        PagoVenta.objects.create(
            ventaid=sale,
            medio_pago="efectivo",
            monto=Decimal("1000.00"),
        )
        concept = ConceptoEgreso.objects.create(
            nombre="ASEO",
            creado_por=self.user,
        )
        Egreso.objects.create(
            concepto=concept,
            monto=Decimal("250.00"),
            medio_pago="efectivo",
            registrado_por=self.user,
            registrado_por_nombre=self.user.nombreusuario,
        )

        today = timezone.localdate().isoformat()
        request = RequestFactory().get(reverse("metricas_negocio_data"), {
            "desde": today,
            "hasta": today,
        })
        request.user = self.user
        response = MetricasNegocioDataView.as_view()(request)
        payload = json.loads(response.content)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["summary"]["total_sales"], 1000.0)
        self.assertEqual(payload["summary"]["expenses_total"], 250.0)
        self.assertEqual(payload["summary"]["remaining_total"], 750.0)
        self.assertNotIn("refunds_total", payload["summary"])
        cash_row = next(
            row for row in payload["tables"]["payment_balance"]
            if row["code"] == "efectivo"
        )
        self.assertEqual(cash_row["sales"], 1000.0)
        self.assertEqual(cash_row["expenses"], 250.0)
        self.assertEqual(cash_row["remaining"], 750.0)
        self.assertNotIn("refunds", cash_row)
        self.assertEqual(payload["tables"]["expenses"][0]["usuario"], "cajero-egresos")
