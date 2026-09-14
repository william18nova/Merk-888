from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from .forms import BuscarEgresosForm, EditarEgresoForm
from .models import ConceptoEgreso, Egreso
from .services.expense_editing import (
    ExpenseEditConflict, can_edit_expenses, edit_operational_expense,
    expense_edit_token, expense_editing_ready,
)
from .services.operational_expenses import OperationalExpenseError
from .services.payment_methods import payment_method_label, payment_method_label_map, payment_method_options


class ExpenseEditAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not can_edit_expenses(request.user):
            raise PermissionDenied("No tienes permiso para editar pagos.")
        if not expense_editing_ready():
            response = render(request, "editar_egreso.html", {"migration_ready": False}, status=503)
        else:
            response = super().dispatch(request, *args, **kwargs)
        response["Cache-Control"] = "no-store, private"
        return response


class EgresosEditarListView(ExpenseEditAccessMixin, View):
    def get(self, request):
        expenses = Egreso.objects.select_related("concepto")
        labels = payment_method_label_map(include_codes=list(
            Egreso.objects.order_by().values_list("medio_pago", flat=True).distinct()
        ))
        form = BuscarEgresosForm(request.GET, payment_methods=sorted(labels.items(), key=lambda item: item[1]))
        if form.is_valid():
            data = form.cleaned_data
            query = data.get("q")
            if query:
                match = Q(concepto__nombre__icontains=query) | Q(registrado_por_nombre__icontains=query)
                if query.isascii() and query.isdigit() and len(query) <= 18:
                    match |= Q(pk=int(query))
                expenses = expenses.filter(match)
            if data.get("desde"):
                expenses = expenses.filter(creado_en__date__gte=data["desde"])
            if data.get("hasta"):
                expenses = expenses.filter(creado_en__date__lte=data["hasta"])
            if data.get("medio"):
                expenses = expenses.filter(medio_pago=data["medio"])
        else:
            expenses = expenses.none()
        total = expenses.aggregate(total=Sum("monto"))["total"] or 0
        page = Paginator(expenses, 30).get_page(request.GET.get("page"))
        for expense in page:
            expense.medio_pago_label = payment_method_label(expense.medio_pago, labels=labels)
        params = request.GET.copy()
        params.pop("page", None)
        return render(request, "pagos_editar_lista.html", {
            "form": form, "page_obj": page, "total": total, "filter_query": params.urlencode(),
        }, status=200 if form.is_valid() else 400)


class EditarEgresoView(ExpenseEditAccessMixin, View):
    def _form(self, expense, data=None, user=None):
        return EditarEgresoForm(
            data, payment_methods=payment_method_options(active_only=True),
            current_method=(expense.medio_pago, payment_method_label(expense.medio_pago)),
            initial={
                "concepto": expense.concepto.nombre, "monto": expense.monto,
                "medio_pago": expense.medio_pago,
                "fecha_pago": timezone.localtime(expense.creado_en, timezone.get_default_timezone()).date(),
                "version": expense_edit_token(expense, user) if data is None else "",
            },
        )

    def _render(self, request, expense, form, status=200):
        history = Paginator(expense.cambios.all(), 20).get_page(request.GET.get("historial"))
        return render(request, "editar_egreso.html", {
            "expense": expense, "form": form, "migration_ready": True,
            "conceptos": ConceptoEgreso.objects.order_by("nombre"), "history_page": history,
        }, status=status)

    def get(self, request, egreso_id):
        expense = get_object_or_404(Egreso.objects.select_related("concepto"), pk=egreso_id)
        return self._render(request, expense, self._form(expense, user=request.user))

    def post(self, request, egreso_id):
        expense = get_object_or_404(Egreso.objects.select_related("concepto"), pk=egreso_id)
        form = self._form(expense, request.POST)
        if not form.is_valid():
            return self._render(request, expense, form, status=400)
        data = form.cleaned_data
        try:
            _expense, changed = edit_operational_expense(
                user=request.user, expense_id=expense.pk, concept=data["concepto"],
                amount=data["monto"], payment_method=data["medio_pago"],
                reason=data["motivo"], version=data["version"],
                payment_date=data.get("fecha_pago"),
            )
        except OperationalExpenseError as exc:
            form.add_error(None, str(exc))
            return self._render(request, expense, form, status=409 if isinstance(exc, ExpenseEditConflict) else 400)
        messages.success(request, f"Pago #{expense.pk} actualizado. La corrección quedó registrada." if changed else "No cambiaste los datos del pago.")
        return redirect("editar_egreso", egreso_id=expense.pk)
