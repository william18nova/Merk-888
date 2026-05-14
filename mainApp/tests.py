from decimal import Decimal

from django.test import RequestFactory, SimpleTestCase

from .views import (
    NequiNotificationWebhookView,
    _looks_like_nequi_payment,
    _parse_nequi_amount,
    _parse_nequi_sender_plain,
)


class NequiWebhookParsingTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_reads_plain_json_body_without_json_content_type(self):
        request = self.factory.post(
            "/api/macrodroid/nequi/",
            data='{"not_title":"Pago recibido","not_text":"Ana Ruiz te envio $10.000"}',
            content_type="text/plain",
        )

        payload = NequiNotificationWebhookView()._payload(request)

        self.assertEqual(payload["not_title"], "Pago recibido")
        self.assertEqual(payload["not_text"], "Ana Ruiz te envio $10.000")

    def test_detects_nequi_payment_text_even_without_app_name(self):
        text = "Ana Ruiz te envio $10.000"
        amount = _parse_nequi_amount(text)

        self.assertEqual(amount, Decimal("10000.00"))
        self.assertTrue(_looks_like_nequi_payment("", text, amount))
        self.assertEqual(_parse_nequi_sender_plain(text), "Ana Ruiz")
