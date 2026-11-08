from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode
from django import forms
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from .services.expense_editing import can_edit_expenses
from .services.expense_reconciliation import closing_report, RECONCILIATION_START
from .services.expense_reconciliation_registration import (
    ClosingExpenseConflict, register_closing_expense, registration_previews,
)
from .services.operational_expenses import OperationalExpenseError


def _pending_review_context(report):
    """Retira lo resuelto de esta vista, sin alterar el reporte ni su auditoría."""
    pending, reconciled = [], []
    for row in report.get('rows', []):
        if row['status'] == 'linked' and not row.get('source_error'):
            reconciled.append(row)
            continue
        if row['status'] == 'linked':
            row = dict(row, status='review', label='Revisar', explanation=(
                'Existe un vínculo auditado, pero el desglose o su total requieren revisión.'))
        pending.append(row)

    reconciled_ids = {payment['id'] for row in reconciled for payment in row.get('candidates', [])}
    pending_ids = {payment['id'] for row in pending for payment in row.get('candidates', [])}
    # Un pago necesario para revisar otra declaración no se oculta.
    hidden_ids = reconciled_ids - pending_ids
    ledger = [payment for payment in report.get('registered_payments', []) if payment['id'] not in hidden_ids]
    summary = [dict(item,
        count=sum(row['status'] == item['code'] for row in pending),
        total=sum((row['amount'] for row in pending if row['status'] == item['code']), Decimal(0)))
        for item in report.get('summary', []) if item['code'] != 'linked']
    return dict(report, rows=pending, summary=summary,
        reconciled_hidden_count=len(reconciled), registered_payments=ledger,
        registered_count=len(ledger),
        registered_base_total=sum((payment['amount'] for payment in ledger), Decimal(0)),
        registered_total=sum((payment.get('total', payment['amount']) for payment in ledger), Decimal(0)))


class ClosingExpenseRegistrationForm(forms.Form):
    source_token = forms.CharField(max_length=2000, widget=forms.HiddenInput)
    confirm_registration = forms.BooleanField(error_messages={
        'required': 'Marca la casilla para confirmar que revisaste los pagos existentes y que este pago falta por registrar.',
    })


class PagosCierresRevisionView(LoginRequiredMixin, View):
    http_method_names=['get', 'post']

    def get(self, request):
        return self._render(request)

    def _render(self, request, *, status=200, registration_error=''):
        if not can_edit_expenses(request.user):
            raise PermissionDenied('No tienes permiso para revisar los pagos del negocio.')
        default_start=max(RECONCILIATION_START, timezone.localdate()-timedelta(days=30))
        query = request.POST if request.method == 'POST' else request.GET
        context={'start':query.get('desde', default_start.isoformat()),
                 'end':query.get('hasta',timezone.localdate().isoformat()),
                 'report_start':RECONCILIATION_START.isoformat(), 'registration_error':registration_error}
        from local_pos.request_context import offline_request
        if offline_request():
            context['error']='Estamos offline. Esta revisión requiere los pagos y cierres actualizados de la base online.'
            return render(request,'pagos_cierres_revision.html',context,status=503)
        try:
            start,end=date.fromisoformat(context['start']),date.fromisoformat(context['end'])
            if not 0 <= (end-start).days <= 92:
                raise ValueError
        except ValueError:
            context['error']='Selecciona un rango válido de hasta 93 días.'
            return render(request,'pagos_cierres_revision.html',context,status=400)
        if start < RECONCILIATION_START:
            context['error']='Esta revisión comienza el 01/11/2026. No se muestran declaraciones ni pagos anteriores a esa fecha.'
            return render(request,'pagos_cierres_revision.html',context,status=400)
        try:
            context.update(_pending_review_context(closing_report(start,end)), checked_at=timezone.now())
            registration_previews(context.get('rows', []), request.user)
        except DatabaseError:
            context['error']='No se pudo consultar la base online. No se modificó ningún pago. Intenta nuevamente cuando se restablezca la conexión.'
            return render(request,'pagos_cierres_revision.html',context,status=503)
        selected=query.get('estado','')
        if selected in {'possible','missing','review'}:
            context['rows']=[r for r in context['rows'] if r['status']==selected]
            context['selected_status']=selected
        response = render(request,'pagos_cierres_revision.html',context,status=status)
        response['Cache-Control'] = 'no-store, private'
        return response

    def post(self, request):
        if not can_edit_expenses(request.user):
            raise PermissionDenied('No tienes permiso para registrar pagos desde cierres.')
        from local_pos.request_context import offline_request
        if offline_request():
            return self._render(request, status=503)
        form = ClosingExpenseRegistrationForm(request.POST)
        if not form.is_valid():
            error = ('Marca la casilla para confirmar que este pago falta por registrar.'
                     if 'confirm_registration' in form.errors else 'La referencia del cierre no es válida. Recarga la página.')
            return self._render(request, status=400, registration_error=error)
        try:
            expense, created = register_closing_expense(user=request.user,
                token=form.cleaned_data['source_token'], confirmed=form.cleaned_data['confirm_registration'])
        except OperationalExpenseError as exc:
            return self._render(request, status=409 if isinstance(exc, ClosingExpenseConflict) else 400,
                                registration_error=str(exc))
        except DatabaseError:
            return self._render(request, status=503,
                registration_error='No se pudo completar el registro. Revisa los pagos antes de intentar de nuevo; no repitas el pago manualmente.')
        messages.success(request, (f'Pago #{expense.pk} registrado en Efectivo con la fecha del cierre y vínculo auditado. No se modificó la caja.'
                                  if created else f'Este detalle ya estaba vinculado al pago #{expense.pk}. No se creó otro pago.'))
        filters = {key: request.POST[key] for key in ('desde', 'hasta', 'estado') if request.POST.get(key)}
        return redirect(reverse('pagos_revisar_cierres') + ('?' + urlencode(filters) if filters else '') + '#closing-declarations')
