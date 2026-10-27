import json
from decimal import Decimal
from django.test import TestCase, RequestFactory, SimpleTestCase
from django.urls import reverse
from .models import AvanceCierreCaja, TurnoCaja, Usuario, Rol, Egreso
from .services.close_progress import DENOMINATIONS, checkpoint, normalize, validate_final
from .test_pos_request_receipts import ReceiptFixture


class CloseProgressTests(ReceiptFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.user.is_staff = True
        self.user.save(update_fields=['is_staff'])
        self.client.force_login(self.user)
        self.turn = TurnoCaja.objects.create(puntopago=self.point, cajero=self.user, estado='CIERRE')
        self.url = reverse('turno_caja_cierre_respaldo', args=[self.turn.pk])
        self.cash = {'step': 'cash', 'invoices': '100', 'payment_details': [{'nombre': 'proveedor', 'monto': '100'}], 'denominations': None}
        self.media = {**self.cash, 'step': 'media', 'denominations': {key: 0 for key in DENOMINATIONS}}
        self.media['denominations']['b5000'] = 2

    def post(self, payload):
        return self.client.post(self.url, json.dumps(payload), content_type='application/json')

    def test_checkpoint_survives_another_browser_without_moving_money(self):
        self.assertEqual(self.post(self.cash).status_code, 200)
        self.assertEqual(self.post(self.media).status_code, 200)
        self.client.logout()
        self.client.force_login(self.user)
        result = self.client.get(self.url)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['progress'], normalize(self.media))
        self.assertEqual(AvanceCierreCaja.objects.count(), 1)
        self.assertFalse(Egreso.objects.exists())
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado, 'CIERRE')
        self.assertIsNone(self.turn.fin)

    def test_stale_tabs_cannot_revert_or_change_confirmed_steps(self):
        self.assertEqual(self.post(self.cash).status_code, 200)
        different = {**self.cash, 'invoices': '90', 'payment_details': [{'nombre': 'X', 'monto': '90'}]}
        self.assertEqual(self.post(different).status_code, 409)
        self.assertEqual(self.post(self.media).status_code, 200)
        self.assertEqual(self.post(self.cash).status_code, 409)
        changed = {**self.media, 'denominations': {key: 1 for key in DENOMINATIONS}}
        self.assertEqual(self.post(changed).status_code, 409)
        self.assertEqual(self.post(self.media).status_code, 200)

    def test_final_close_cannot_change_checkpoint_values(self):
        self.post(self.media)
        validate_final(self.turn, '100', self.cash['payment_details'], 10000)
        for total, details, cash in [('99', [{'nombre': 'X', 'monto': '99'}], 10000), ('100', self.cash['payment_details'], 9000)]:
            with self.assertRaises(ValueError):
                validate_final(self.turn, total, details, cash)

    def test_other_operator_and_closed_turn_are_rejected(self):
        other = Usuario.objects.create_user('other-checkpoint', 'test-only', rolid=Rol.objects.create(nombre='Cajero'))
        request = RequestFactory().post(self.url, json.dumps(self.cash), content_type='application/json')
        request.user = other
        self.assertEqual(checkpoint(request, self.turn.pk).status_code, 403)
        TurnoCaja.objects.filter(pk=self.turn.pk).update(estado='CERRADO')
        self.assertEqual(self.post(self.cash).status_code, 409)
        self.assertFalse(AvanceCierreCaja.objects.exists())

    def test_invalid_payload_and_total_are_rejected(self):
        for data in [[], None, {}, {**self.cash, 'invoices': '200'}, {**self.media, 'denominations': {}}]:
            self.assertEqual(self.post(data).status_code, 400)
        self.assertEqual(self.client.post(self.url, '{invalid', content_type='application/json').status_code, 400)
        self.assertFalse(AvanceCierreCaja.objects.exists())

    def test_legacy_media_checkpoint_can_be_imported(self):
        self.assertEqual(self.post(self.media).status_code, 200)
        self.assertEqual(self.client.get(self.url).json()['progress'], normalize(self.media))
