from datetime import date, timedelta
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.shortcuts import render
from django.utils import timezone
from django.views import View
from .services.expense_editing import can_edit_expenses
from .services.expense_reconciliation import closing_report, RECONCILIATION_START


class PagosCierresRevisionView(LoginRequiredMixin, View):
    http_method_names=['get']

    def get(self, request):
        if not can_edit_expenses(request.user):
            raise PermissionDenied('No tienes permiso para revisar los pagos del negocio.')
        default_start=max(RECONCILIATION_START, timezone.localdate()-timedelta(days=30))
        context={'start':request.GET.get('desde', default_start.isoformat()),
                 'end':request.GET.get('hasta',timezone.localdate().isoformat()),
                 'report_start':RECONCILIATION_START.isoformat()}
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
            context.update(closing_report(start,end), checked_at=timezone.now())
        except DatabaseError:
            context['error']='No se pudo consultar la base online. No se modificó ningún pago. Intenta nuevamente cuando se restablezca la conexión.'
            return render(request,'pagos_cierres_revision.html',context,status=503)
        selected=request.GET.get('estado','')
        if selected in {'linked','possible','missing','review'}:
            context['rows']=[r for r in context['rows'] if r['status']==selected]
            context['selected_status']=selected
        return render(request,'pagos_cierres_revision.html',context)
