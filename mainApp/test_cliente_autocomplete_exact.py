from django.test import TestCase
from django.urls import reverse

from mainApp.models import Cliente, ClienteEspecial, Empleado, Rol, Usuario
from mainApp.services.special_discount import SPECIAL_CLIENT_KEY


class ClienteAutocompleteExactTests(TestCase):
    def setUp(self):
        web_master_role = Rol.objects.create(
            nombre="Web Master",
            descripcion="Administración total",
        )
        self.user = Usuario.objects.create_user(
            nombreusuario="cliente-exacto-test",
            password="test-pass",
            rolid=web_master_role,
        )
        self.client.force_login(self.user)

        self.employee = Empleado.objects.create(
            nombre="William",
            apellido="Nova",
            telefono="3000000001",
            email="william.autocomplete@example.com",
            direccion="Dirección de prueba",
            puesto="Administrador",
            numerodocumento="123456789",
            usuarioid=self.user,
        )
        self.employee_client = Cliente.objects.get(
            numerodocumento="123456789"
        )
        ClienteEspecial.objects.create(
            cliente=self.employee_client,
            clave=SPECIAL_CLIENT_KEY,
            activo=True,
        )
        self.regular_client = Cliente.objects.create(
            nombre="Cliente",
            apellido="Regular",
            telefono="3000000002",
            email="regular.autocomplete@example.com",
            numerodocumento="987654321",
        )
        self.url = reverse("cliente_autocomplete")

    def test_exact_id_ignores_term_and_preserves_employee_and_special_flags(self):
        response = self.client.get(
            self.url,
            {
                "cliente_id": str(self.employee_client.pk),
                "term": "texto que no coincide",
                "page": "99",
                "limit": "30",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["has_more"])
        self.assertEqual(len(payload["results"]), 1)

        result = payload["results"][0]
        self.assertEqual(result["id"], self.employee_client.pk)
        self.assertEqual(result["documento"], "123456789")
        self.assertTrue(result["is_employee"])
        self.assertEqual(result["employee_name"], str(self.employee))
        self.assertTrue(result["employee_has_user"])
        self.assertTrue(result["employee_is_web_master"])
        self.assertTrue(result["is_merk2888"])

    def test_exact_regular_client_has_current_false_flags(self):
        response = self.client.get(
            self.url,
            {"cliente_id": str(self.regular_client.pk)},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["has_more"])
        self.assertEqual(len(payload["results"]), 1)

        result = payload["results"][0]
        self.assertEqual(result["id"], self.regular_client.pk)
        self.assertFalse(result["is_employee"])
        self.assertEqual(result["employee_name"], "")
        self.assertFalse(result["employee_has_user"])
        self.assertFalse(result["employee_is_web_master"])
        self.assertFalse(result["is_merk2888"])

    def test_invalid_exact_id_returns_an_empty_result(self):
        for invalid_id in ("", "abc", "12x", "-1", "1.5"):
            with self.subTest(cliente_id=invalid_id):
                response = self.client.get(
                    self.url,
                    {"cliente_id": invalid_id},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.json(),
                    {"results": [], "has_more": False},
                )

    def test_unknown_numeric_exact_id_returns_an_empty_result(self):
        response = self.client.get(
            self.url,
            {"cliente_id": "999999999"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"results": [], "has_more": False},
        )

