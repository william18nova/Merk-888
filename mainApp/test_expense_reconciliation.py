from datetime import date
from decimal import Decimal
from unittest.mock import patch
from types import SimpleNamespace
from django.test import SimpleTestCase, RequestFactory
from django.core.exceptions import PermissionDenied
from mainApp.services.expense_reconciliation import reconcile, similarity
from mainApp.expense_reconciliation_views import PagosCierresRevisionView


class ReconciliationTests(SimpleTestCase):
    def declaration(self, turn=1, name='TURNO FABIAN', amount='70000'):
        return dict(turn=turn,date=date(2026,11,1),afternoon=True,payments=[dict(nombre=name,monto=amount)])

    def expense(self, name='FABIÁN', amount='70000', day=1, method='efectivo'):
        return dict(id=10,date=date(2026,11,day),name=name,amount=Decimal(amount),method=method)

    def test_similar_name_is_only_possible(self):
        self.assertEqual(reconcile([self.declaration()],[self.expense()],{})[0]['status'],'possible')

    def test_amount_alone_never_matches(self):
        self.assertEqual(reconcile([self.declaration()],[self.expense('BIBIANA')],{})[0]['status'],'missing')

    def test_audited_link_confirmed_and_reserved(self):
        rows=reconcile([self.declaration(),self.declaration(2)],[self.expense()],{(1,0):{10}})
        self.assertEqual([r['status'] for r in rows],['linked','missing'])

    def test_shared_candidate_requires_review(self):
        rows=reconcile([self.declaration(),self.declaration(2)],[self.expense()],{})
        self.assertTrue(all(r['status']=='review' for r in rows))

    def test_changed_audit_amount_requires_review(self):
        self.assertEqual(reconcile([self.declaration()],[self.expense(amount='80000')],{(1,0):{10}})[0]['status'],'review')

    def test_changed_audit_name_requires_review(self):
        self.assertEqual(reconcile([self.declaration()],[self.expense(name='BIBIANA')],{(1,0):{10}})[0]['status'],'review')

    def test_different_suppliers_not_matched(self):
        self.assertLess(similarity('AREPAS ALBERTO','AREPAS NACH'),.72)

    def test_known_alias_and_singular(self):
        self.assertEqual(similarity('DIEGO','Fabián'),1)
        self.assertEqual(similarity('30 EMPANADAS','EMPANADA'),1)

    def test_bad_amount_and_empty_name(self):
        self.assertEqual(reconcile([self.declaration(amount='NaN')],[],{})[0]['status'],'review')
        self.assertEqual(similarity('',''),0)

    def test_split_audited_expenses(self):
        e1=self.expense('LINA','80000');e2=dict(self.expense('MIGUEL','50000'),id=11)
        row=reconcile([self.declaration(name='LINA TURNO 80000 MIGUEL PRESTAMO 50000',amount='130000')],[e1,e2],{(1,0):{10,11}})[0]
        self.assertEqual(row['status'],'linked')

    def test_neighbor_date_and_tax_base(self):
        row=reconcile([self.declaration()],[self.expense(day=2,method='nequi')],{})[0]
        self.assertEqual(row['status'],'possible')

    def test_declarations_before_activation_are_hidden(self):
        previous=dict(self.declaration(),date=date(2026,10,31))
        self.assertEqual(reconcile([previous],[self.expense()],{}),[])

    def test_old_candidate_is_hidden_even_when_within_one_day(self):
        previous=dict(self.expense(),date=date(2026,10,31))
        row=reconcile([self.declaration()],[previous],{})[0]
        self.assertEqual(row['status'],'missing')
        self.assertEqual(row['candidates'],[])

    def test_old_audited_candidate_is_also_hidden(self):
        previous=dict(self.expense(),date=date(2026,10,31))
        row=reconcile([self.declaration()],[previous],{(1,0):{10}})[0]
        self.assertEqual(row['candidates'],[])


class ReconciliationViewTests(SimpleTestCase):
    def request(self,query=None):
        r=RequestFactory().get('/caja/pagos/revisar-cierres/',query or {})
        r.user=SimpleNamespace(is_authenticated=True)
        return r

    @patch('mainApp.expense_reconciliation_views.can_edit_expenses',return_value=False)
    def test_permission_checked_before_queries(self,_):
        with self.assertRaises(PermissionDenied):
            PagosCierresRevisionView.as_view()(self.request())

    @patch('mainApp.expense_reconciliation_views.render')
    @patch('local_pos.request_context.offline_request',return_value=False)
    @patch('mainApp.expense_reconciliation_views.can_edit_expenses',return_value=True)
    @patch('mainApp.expense_reconciliation_views.closing_report')
    def test_invalid_dates_no_queries(self,report,*_):
        PagosCierresRevisionView.as_view()(self.request({'desde':'2026-11-02','hasta':'2026-11-01'}))
        report.assert_not_called()

    @patch('mainApp.expense_reconciliation_views.render')
    @patch('local_pos.request_context.offline_request',return_value=True)
    @patch('mainApp.expense_reconciliation_views.can_edit_expenses',return_value=True)
    @patch('mainApp.expense_reconciliation_views.closing_report')
    def test_offline_does_not_claim_current_report(self,report,*_):
        PagosCierresRevisionView.as_view()(self.request())
        report.assert_not_called()

    def test_post_not_supported(self):
        r=RequestFactory().post('/caja/pagos/revisar-cierres/')
        r.user=SimpleNamespace(is_authenticated=True)
        self.assertEqual(PagosCierresRevisionView.as_view()(r).status_code,405)

    @patch('mainApp.expense_reconciliation_views.render')
    @patch('local_pos.request_context.offline_request',return_value=False)
    @patch('mainApp.expense_reconciliation_views.can_edit_expenses',return_value=True)
    @patch('mainApp.expense_reconciliation_views.closing_report')
    def test_long_range_rejected(self,report,*_):
        PagosCierresRevisionView.as_view()(self.request({'desde':'2025-01-01','hasta':'2026-11-01'}))
        report.assert_not_called()

    @patch('mainApp.expense_reconciliation_views.render')
    @patch('local_pos.request_context.offline_request',return_value=False)
    @patch('mainApp.expense_reconciliation_views.can_edit_expenses',return_value=True)
    @patch('mainApp.expense_reconciliation_views.closing_report')
    def test_old_range_rejected_by_server(self,report,permission,offline,render):
        PagosCierresRevisionView.as_view()(self.request({'desde':'2026-10-31','hasta':'2026-11-01'}))
        report.assert_not_called()
        self.assertEqual(render.call_args.kwargs['status'],400)

    @patch('mainApp.expense_reconciliation_views.render')
    @patch('local_pos.request_context.offline_request',return_value=False)
    @patch('mainApp.expense_reconciliation_views.can_edit_expenses',return_value=True)
    @patch('mainApp.expense_reconciliation_views.timezone.localdate',return_value=date(2026,11,1))
    @patch('mainApp.expense_reconciliation_views.closing_report',return_value={})
    def test_default_starts_at_activation(self,report,*_):
        PagosCierresRevisionView.as_view()(self.request())
        report.assert_called_once_with(date(2026,11,1),date(2026,11,1))

    @patch('mainApp.expense_reconciliation_views.render')
    @patch('local_pos.request_context.offline_request',return_value=False)
    @patch('mainApp.expense_reconciliation_views.can_edit_expenses',return_value=True)
    @patch('mainApp.expense_reconciliation_views.timezone.localdate',return_value=date(2026,11,3))
    @patch('mainApp.expense_reconciliation_views.closing_report',return_value={})
    def test_activation_is_not_moving_today(self,report,*_):
        PagosCierresRevisionView.as_view()(self.request())
        report.assert_called_once_with(date(2026,11,1),date(2026,11,3))

    @patch('mainApp.expense_reconciliation_views.render')
    @patch('local_pos.request_context.offline_request',return_value=False)
    @patch('mainApp.expense_reconciliation_views.can_edit_expenses',return_value=True)
    def test_database_failure_shows_notice(self,_,offline,render):
        from django.db import DatabaseError
        with patch('mainApp.expense_reconciliation_views.closing_report',side_effect=DatabaseError):
            PagosCierresRevisionView.as_view()(self.request())
        self.assertEqual(render.call_args.kwargs['status'],503)
