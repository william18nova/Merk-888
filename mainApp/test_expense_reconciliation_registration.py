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

    def extra_source(self, start, *, name='TURNO CAMILA', amount='70000', state='CERRADO', end=None):
        turn = TurnoCaja.objects.create(cajero=self.cashier, puntopago=self.point, estado=state)
        TurnoCaja.objects.filter(pk=turn.pk).update(inicio=timezone.make_aware(start, CO),
            fin=timezone.make_aware(end, CO) if end else None)
        turn.refresh_from_db()
        source = DesglosePagosCierre.objects.create(turno=turn, registrado_por=self.cashier,
            total=Decimal(amount), pagos=[{'nombre': name, 'monto': amount}])
        return source

    def test_page_has_per_declaration_confirmation_and_no_get_writes(self):
        response = self.client.get(self.url, self.query)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Registrar este pago')
        self.assertContains(response, 'Confirmar y registrar pago')
        self.assertContains(response, 'confirm_registration')
        self.assertContains(response, 'Incluye todos los turnos cerrados')
        self.assertContains(response, 'Inicio 14:00')
        self.assertNotContains(response, 'cierres de tarde')
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

    def test_only_closed_turns_after_activation(self):
        for state, stamp in [('ABIERTO', datetime(2026, 11, 2, 7)),
                             ('CIERRE', datetime(2026, 11, 2, 20)),
                             ('CERRADO', datetime(2026, 10, 31, 23, 59))]:
            TurnoCaja.objects.filter(pk=self.turn.pk).update(estado=state, inicio=timezone.make_aware(stamp, CO))
            with self.subTest(state=state, stamp=stamp), self.assertRaises(ClosingExpenseConflict):
                self.apply(self.signed())
        self.assertFalse(Egreso.objects.exists())

    def test_report_and_page_include_all_start_times_in_colombia(self):
        times = [(0, 0), (7, 0), (11, 59), (12, 0), (19, 59), (20, 0), (23, 59)]
        sources = [self.extra_source(datetime(2026, 11, 2, hour, minute)) for hour, minute in times]
        # A declaration is eligible only after the turn is actually closed and
        # belongs to the date range by local start, not by UTC or end time.
        self.extra_source(datetime(2026, 11, 2, 8), state='ABIERTO')
        self.extra_source(datetime(2026, 11, 2, 9), state='CIERRE')
        self.extra_source(datetime(2026, 10, 31, 23, 59), end=datetime(2026, 11, 2, 1))
        self.extra_source(datetime(2026, 11, 3, 0))
        empty = self.extra_source(datetime(2026, 11, 2, 6))
        DesglosePagosCierre.objects.filter(pk=empty.pk).update(total=Decimal(0), pagos=[])
        report = closing_report(date(2026, 11, 2), date(2026, 11, 2))
        expected = {self.turn.pk, *(source.turno_id for source in sources)}
        self.assertEqual({row['turn'] for row in report['rows']}, expected)
        self.assertEqual(report['source_count'], 8)
        missing = next(item for item in report['summary'] if item['code'] == 'missing')
        self.assertEqual((missing['count'], missing['total']), (8, Decimal('560000')))
        starts = {(row['started_at'].hour, row['started_at'].minute) for row in report['rows']}
        self.assertEqual(starts, {*times, (14, 0)})
        response = self.client.get(self.url, self.query)
        self.assertContains(response, '8 cierres con detalle')
        for hour, minute in times:
            with self.subTest(hour=hour, minute=minute):
                self.assertContains(response, f'Inicio {hour:02}:{minute:02}')
        self.assertEqual({row['turn'] for row in response.context['rows']}, expected)
        self.assertTrue(all('registration' in row for row in response.context['rows']))
        self.assertFalse(Egreso.objects.exists())

    def test_shared_payment_in_morning_and_afternoon_requires_review_for_both(self):
        morning = self.extra_source(datetime(2026, 11, 2, 7))
        expense = register_operational_expense(user=self.user, concept='CAMILA', amount='70000', payment_method='efectivo')
        Egreso.objects.filter(pk=expense.pk).update(creado_en=self.turn.inicio)
        report = closing_report(date(2026, 11, 2), date(2026, 11, 2))
        registration_previews(report['rows'], self.user)
        self.assertEqual({row['turn'] for row in report['rows']}, {morning.turno_id, self.turn.pk})
        self.assertTrue(all(row['status'] == 'review' and 'registration' not in row for row in report['rows']))
        self.assertEqual({ref['turn'] for ref in report['registered_payments'][0]['references']},
                         {morning.turno_id, self.turn.pk})

    def test_newest_shifts_first_preserves_source_row_order_and_filters(self):
        # Create records out of date order; ties use the newest shift ID.
        older = self.extra_source(datetime(2026, 11, 1, 23, 59), name='DECLARACION ANTIGUA')
        newest = self.extra_source(datetime(2026, 11, 3, 0, 15), name='DECLARACION RECIENTE')
        morning = self.extra_source(datetime(2026, 11, 2, 7), name='DECLARACION MANANA')
        tied = self.extra_source(datetime(2026, 11, 3, 0, 15), name='DECLARACION PRIMERA')
        tied.pagos = [{'nombre': 'DECLARACION PRIMERA', 'monto': '30000'},
                      {'nombre': 'DECLARACION SEGUNDA', 'monto': '40000'}]
        tied.save()
        expected = [(tied.turno_id, 0), (tied.turno_id, 1), (newest.turno_id, 0),
                    (self.turn.pk, 0), (morning.turno_id, 0), (older.turno_id, 0)]
        report = closing_report(date(2026, 11, 1), date(2026, 11, 3))
        self.assertEqual([(row['turn'], row['index']) for row in report['rows']], expected)
        for status in ('', 'missing'):
            with self.subTest(status=status):
                response = self.client.get(self.url, {'desde': '2026-11-01', 'hasta': '2026-11-03', 'estado': status})
                self.assertEqual(response.status_code, 200)
                self.assertEqual([(row['turn'], row['index']) for row in response.context['rows']], expected)
                self.assertContains(response, 'Más recientes primero')
                html = response.content.decode()
                names = ['DECLARACION PRIMERA', 'DECLARACION SEGUNDA', 'DECLARACION RECIENTE',
                         'TURNO CAMILA', 'DECLARACION MANANA', 'DECLARACION ANTIGUA']
                self.assertEqual([html.index(name) for name in names], sorted(html.index(name) for name in names))
        self.assertFalse(Egreso.objects.exists())
        self.assertFalse(CambioEgreso.objects.exists())

    def test_morning_night_and_overnight_can_register_without_duplicates_or_cash_changes(self):
        cases = [(7, 0, 'AREPAS', '30000'), (20, 0, 'VIGILANCIA', '40000'),
                 (0, 30, 'TRANSPORTE', '50000'), (23, 59, 'INSUMOS', '60000')]
        for index, (hour, minute, name, amount) in enumerate(cases, start=1):
            with self.subTest(hour=hour, minute=minute):
                source = self.extra_source(datetime(2026, 11, 2, hour, minute), name=name, amount=amount,
                    end=datetime(2026, 11, 3, 2) if hour >= 20 else datetime(2026, 11, 2, 14))
                before = TurnoCaja.objects.values().get(pk=source.turno_id)
                row = next(row for row in self.rows() if row['turn'] == source.turno_id)
                token = row['registration']['token']
                expense, created = self.apply(token)
                same, created_again = self.apply(token)
                self.assertTrue(created)
                self.assertFalse(created_again)
                self.assertEqual(same.pk, expense.pk)
                self.assertEqual(timezone.localtime(expense.creado_en, CO).date(), date(2026, 11, 2))
                self.assertEqual((expense.concepto.nombre, expense.monto_base), (name, Decimal(amount)))
                self.assertEqual((Egreso.objects.count(), CambioEgreso.objects.count()), (index, index))
                linked = next(row for row in self.rows() if row['turn'] == source.turno_id)
                self.assertEqual(linked['status'], 'linked')
                self.assertEqual(TurnoCaja.objects.values().get(pk=source.turno_id), before)
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal('123456'))

    def test_morning_post_retains_status_filter_and_registers_source_date(self):
        TurnoCaja.objects.filter(pk=self.turn.pk).update(inicio=timezone.make_aware(datetime(2026, 11, 2, 7), CO))
        response = self.client.post(self.url, {'source_token': self.token(), 'confirm_registration': '1',
            'estado': 'missing', **self.query})
        self.assertEqual(response.status_code, 302)
        self.assertIn('estado=missing', response.url)
        expense = Egreso.objects.get()
        self.assertEqual(timezone.localtime(expense.creado_en, CO).date(), date(2026, 11, 2))
        response = self.client.get(response.url)
        self.assertEqual(response.context['rows'], [])
        self.assertEqual([payment['id'] for payment in response.context['registered_payments']], [expense.pk])

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
