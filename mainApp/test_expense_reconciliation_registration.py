from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils import timezone

from .models import (CambioEgreso, DesglosePagosCierre, Egreso, MetodoPago,
                     PuntosPago, Rol, Sucursal, TurnoCaja, Usuario)
from .services.expense_reconciliation import CO, closing_report, source_version
from .services.expense_reconciliation_registration import (
    ClosingExpenseConflict, SALT, register_closing_expense, registration_previews,
)
from .services.operational_expenses import OperationalExpenseError, register_operational_expense


class ClosingExpenseRegistrationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = Usuario.objects.create_user('Web Master conciliación', rolid=Rol.objects.create(nombre='Web Master'))
        self.cashier = Usuario.objects.create_user('Cajera cierre', rolid=Rol.objects.create(nombre='Cajero'))
        self.branch = Sucursal.objects.create(nombre='Yerbabuena de prueba')
        self.point = PuntosPago.objects.create(nombre='Caja de prueba', sucursalid=self.branch, dinerocaja=Decimal('123456'))
        self.cash = MetodoPago.objects.create(codigo='efectivo', nombre='Efectivo', activo=True, es_efectivo=True)
        self.turn = TurnoCaja.objects.create(cajero=self.cashier, puntopago=self.point, estado='CERRADO',
                                           esperado_total=Decimal('800000'), real_total=Decimal('800000'))
        TurnoCaja.objects.filter(pk=self.turn.pk).update(inicio=timezone.make_aware(datetime(2026, 11, 2, 14), CO))
        self.turn.refresh_from_db()
        self.source = DesglosePagosCierre.objects.create(turno=self.turn, registrado_por=self.cashier,
            total=Decimal('70000'), pagos=[{'nombre': 'TURNO CAMILA', 'monto': '70000.00'}])
        self.url = reverse('pagos_revisar_cierres')
        self.query = {'desde': '2026-11-02', 'hasta': '2026-11-02'}
        self.client.force_login(self.user)

    def rows(self):
        rows = closing_report(date(2026, 11, 2), date(2026, 11, 2))['rows']
        registration_previews(rows, self.user)
        return rows

    def token(self):
        return self.rows()[0]['registration']['token']

    def apply(self, token=None, **changes):
        return register_closing_expense(**dict({'user': self.user, 'token': token or self.token(), 'confirmed': True}, **changes))

    def signed(self, **changes):
        self.source.refresh_from_db()
        data = dict(user=self.user.pk, turn=self.turn.pk, row=0, version=source_version(self.source), cash_tax=False)
        data.update(changes)
        return signing.dumps(data, salt=SALT)

    def test_page_has_per_declaration_confirmation_and_no_get_writes(self):
        response = self.client.get(self.url, self.query)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registrar este pago')
        self.assertContains(response, 'Confirmar y registrar pago')
        self.assertContains(response, 'confirm_registration')
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.assertFalse(Egreso.objects.exists())
        self.assertFalse(CambioEgreso.objects.exists())

    def test_registers_at_original_date_and_audited_link_without_touching_close_or_cash(self):
        before_turn = TurnoCaja.objects.values().get(pk=self.turn.pk)
        before_source = DesglosePagosCierre.objects.values().get(pk=self.turn.pk)
        expense, created = self.apply()
        self.assertTrue(created)
        self.assertEqual(expense.concepto.nombre, 'TURNO CAMILA')
        self.assertEqual(expense.monto_base, Decimal('70000'))
        self.assertEqual(expense.medio_pago, 'efectivo')
        self.assertEqual(timezone.localtime(expense.creado_en, CO).date(), date(2026, 11, 2))
        self.assertEqual(expense.registrado_por, self.user)
        self.assertEqual(CambioEgreso.objects.count(), 1)
        self.assertTrue(expense.cambios.get().motivo.startswith(f'CONCILIACION WEB CIERRE {self.turn.pk}/0 |'))
        self.assertEqual(self.rows()[0]['status'], 'linked')
        self.assertEqual(TurnoCaja.objects.values().get(pk=self.turn.pk), before_turn)
        self.assertEqual(DesglosePagosCierre.objects.values().get(pk=self.turn.pk), before_source)
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal('123456'))

    def test_double_click_and_new_signed_preview_cannot_create_second_payment(self):
        token = self.token()
        first, created = self.apply(token)
        second, created_again = self.apply(token)
        third, new_again = self.apply(self.signed())
        self.assertTrue(created)
        self.assertFalse(created_again or new_again)
        self.assertEqual({first.pk, second.pk, third.pk}, {first.pk})
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(CambioEgreso.objects.count(), 1)

    def test_existing_payment_outside_report_range_still_prevents_duplicate(self):
        token = self.token()
        expense, _ = self.apply(token)
        Egreso.objects.filter(pk=expense.pk).update(creado_en=timezone.make_aware(datetime(2026, 10, 20, 14), CO))
        other, created = self.apply(token)
        self.assertFalse(created)
        self.assertEqual(other.pk, expense.pk)
        self.assertEqual(Egreso.objects.count(), 1)

    def test_legacy_three_part_source_marker_also_prevents_duplicate(self):
        token = self.token()
        expense = register_operational_expense(user=self.user, concept='CAMILA', amount='70000', payment_method='efectivo')
        Egreso.objects.filter(pk=expense.pk).update(creado_en=timezone.make_aware(datetime(2026, 10, 20, 14), CO))
        CambioEgreso.objects.create(egreso=expense, usuario=self.user, usuario_nombre=self.user.nombreusuario,
            motivo=f'CONCILIACION CIERRE {self.turn.pk}/0/2 | Registro anterior', anterior={}, nuevo={})
        existing, created = self.apply(token)
        self.assertFalse(created)
        self.assertEqual(existing.pk, expense.pk)
        self.assertEqual(Egreso.objects.count(), 1)

    def test_possible_match_is_not_given_a_register_button(self):
        register_operational_expense(user=self.user, concept='CAMILA', amount='70000', payment_method='efectivo')
        Egreso.objects.update(creado_en=self.turn.inicio)
        row = self.rows()[0]
        self.assertEqual(row['status'], 'possible')
        self.assertNotIn('registration', row)

    def test_match_appearing_after_preview_is_rechecked(self):
        token = self.token()
        register_operational_expense(user=self.user, concept='CAMILA', amount='70000', payment_method='efectivo')
        Egreso.objects.update(creado_en=self.turn.inicio)
        with self.assertRaisesMessage(ClosingExpenseConflict, 'posible coincidencia'):
            self.apply(token)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertFalse(CambioEgreso.objects.exists())

    def test_source_edit_or_reordered_items_invalidates_old_token(self):
        token = self.token()
        self.source.pagos = [{'nombre': 'OTRO CONCEPTO', 'monto': '70000.00'}]
        self.source.save()
        with self.assertRaisesMessage(ClosingExpenseConflict, 'El cierre cambió'):
            self.apply(token)
        self.assertFalse(Egreso.objects.exists())

    def test_invalid_total_or_missing_index_rejected_before_writing(self):
        self.source.total = Decimal('70001')
        self.source.save()
        with self.assertRaisesMessage(ClosingExpenseConflict, 'suma'):
            self.apply(self.signed())
        self.source.total = Decimal('70000')
        self.source.save()
        with self.assertRaises(ClosingExpenseConflict):
            self.apply(self.signed(row=1))
        self.assertFalse(Egreso.objects.exists())

    def test_only_closed_afternoon_turns_after_activation(self):
        for state, stamp in [('ABIERTO', datetime(2026, 11, 2, 14)),
                             ('CERRADO', datetime(2026, 11, 2, 7)),
                             ('CERRADO', datetime(2026, 10, 31, 14))]:
            TurnoCaja.objects.filter(pk=self.turn.pk).update(estado=state, inicio=timezone.make_aware(stamp, CO))
            with self.subTest(state=state, stamp=stamp), self.assertRaises(ClosingExpenseConflict):
                self.apply(self.signed())
        self.assertFalse(Egreso.objects.exists())

    def test_foreign_tampered_and_expired_tokens_cannot_write(self):
        token = self.token()
        other = Usuario.objects.create_user('Otro Web Master', rolid=self.user.rolid)
        with self.assertRaises(ClosingExpenseConflict):
            self.apply(token, user=other)
        with self.assertRaises(ClosingExpenseConflict):
            self.apply(token + 'tampered')
        with patch('mainApp.services.expense_reconciliation_registration.signing.loads', side_effect=signing.SignatureExpired):
            with self.assertRaises(ClosingExpenseConflict):
                self.apply(token)
        self.assertFalse(Egreso.objects.exists())

    def test_permission_and_confirmation_are_required_again_on_post(self):
        token = self.token()
        with self.assertRaises(PermissionDenied):
            self.apply(token, user=self.cashier)
        with self.assertRaises(OperationalExpenseError):
            self.apply(token, confirmed=False)
        self.client.force_login(self.cashier)
        for response in (self.client.get(self.url, self.query),
                         self.client.post(self.url, {'source_token': token, 'confirm_registration': '1', **self.query})):
            self.assertIn(response.status_code, (302, 403))
        self.assertFalse(Egreso.objects.exists())

    def test_offline_service_cannot_register_or_queue_an_expense(self):
        token = self.token()
        with patch('local_pos.request_context.offline_request', return_value=True):
            with self.assertRaisesMessage(OperationalExpenseError, 'requiere conexión'):
                self.apply(token)
            response = self.client.post(self.url, {'source_token': token, 'confirm_registration': '1', **self.query})
        self.assertEqual(response.status_code, 503)
        self.assertFalse(Egreso.objects.exists())

    def test_tax_is_shown_and_calculated_once_using_cash_configuration(self):
        self.cash.aplica_4xmil_egresos = True
        self.cash.save()
        row = self.rows()[0]
        self.assertEqual(row['registration']['tax'], Decimal('280'))
        expense, _ = self.apply(row['registration']['token'])
        self.assertEqual(expense.monto, Decimal('70280'))
        self.assertEqual(expense.monto_base, Decimal('70000'))
        self.assertEqual(expense.impuesto_4xmil, Decimal('280'))
        self.assertEqual(self.rows()[0]['status'], 'linked')

    def test_changed_tax_rule_or_disabled_cash_rejected_without_partial_expense(self):
        token = self.token()
        self.cash.aplica_4xmil_egresos = True
        self.cash.save()
        with self.assertRaisesMessage(OperationalExpenseError, 'configuración'):
            self.apply(token)
        self.cash.activo = False
        self.cash.save()
        with self.assertRaises(OperationalExpenseError):
            self.apply(token)
        self.assertFalse(Egreso.objects.exists())
        self.assertFalse(CambioEgreso.objects.exists())

    def test_audit_failure_rolls_back_new_expense_and_date_change(self):
        token = self.token()
        with patch('mainApp.models.CambioEgreso.objects.create', side_effect=OperationalExpenseError('falló auditoría')):
            with self.assertRaises(OperationalExpenseError):
                self.apply(token)
        self.assertFalse(Egreso.objects.exists())

    def test_post_ignores_invented_values_and_uses_original_source_then_redirects(self):
        response = self.client.post(self.url, {'source_token': self.token(), 'confirm_registration': '1',
            'concepto': 'NO USAR', 'monto': '1', 'medio_pago': 'nequi', 'fecha': '2025-01-01', **self.query})
        self.assertEqual(response.status_code, 302)
        expense = Egreso.objects.get()
        self.assertEqual((expense.concepto.nombre, expense.monto_base, expense.medio_pago), ('TURNO CAMILA', Decimal('70000'), 'efectivo'))
        response = self.client.get(response.url)
        self.assertContains(response, 'vínculo auditado')
        self.assertContains(response, 'Conciliado con auditoría')
        self.assertNotContains(response, 'Confirmar y registrar pago')

    def test_post_without_explicit_confirmation_has_no_side_effect(self):
        response = self.client.post(self.url, {'source_token': self.token(), **self.query})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Marca la casilla', status_code=400)
        self.assertFalse(Egreso.objects.exists())

    def test_csrf_is_required(self):
        guarded = Client(enforce_csrf_checks=True)
        guarded.force_login(self.user)
        response = guarded.post(self.url, {'source_token': self.token(), 'confirm_registration': '1', **self.query})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Egreso.objects.exists())


class ClosingExpenseConcurrencyTests(TransactionTestCase):
    setUp = ClosingExpenseRegistrationTests.setUp
    rows = ClosingExpenseRegistrationTests.rows
    token = ClosingExpenseRegistrationTests.token

    @skipUnlessDBFeature('has_select_for_update')
    def test_two_simultaneous_confirmations_create_one_audited_payment(self):
        token, user_id = self.token(), self.user.pk
        barrier = Barrier(2)

        def register():
            connections.close_all()
            try:
                user = Usuario.objects.select_related('rolid').get(pk=user_id)
                barrier.wait(timeout=10)
                expense, created = register_closing_expense(user=user, token=token, confirmed=True)
                return expense.pk, created
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(register) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(len({pk for pk, _created in results}), 1)
        self.assertEqual(sum(created for _pk, created in results), 1)
        self.assertEqual(Egreso.objects.count(), 1)
        self.assertEqual(CambioEgreso.objects.count(), 1)
