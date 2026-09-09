from decimal import Decimal
import logging
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.views import View

from .models import OperacionPTM, TurnoCaja
from .permissions import user_can_access_url_name
from .services.ptm import registrar_operacion_ptm, resumen_ptm


class OperacionPTMForm(forms.Form):
    turno = forms.ModelChoiceField(queryset=TurnoCaja.objects.none(), label="Tu turno de caja")
    tipo = forms.ChoiceField(choices=[("recarga", "PTM RECARGA O PAGOS — entra efectivo"),
                                    ("retiro", "PTM RETIROS — sale efectivo")], label="Operación")
    monto = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"),
                               label="Valor de la operación (COP)")
    referencia = forms.CharField(max_length=100, label="Referencia del comprobante PTM")
    confirmada = forms.BooleanField(label="Confirmo que PTM aprobó la operación y que entregué o recibí este efectivo.")
    solicitud_id = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, actor, **kwargs):
        super().__init__(*args, **kwargs)
        # Incluye turnos cerrados sólo para poder reconocer reenvíos idénticos.
        self.fields["turno"].queryset = TurnoCaja.objects.filter(cajero=actor).select_related("puntopago").order_by("-inicio")
        self.fields["turno"].label_from_instance = lambda t: f"#{t.pk} · {t.puntopago.nombre} · {t.estado}"
        if not self.is_bound:
            self.fields["turno"].queryset = self.fields["turno"].queryset.filter(estado="ABIERTO")
        for field in ("monto", "referencia"):
            self.fields[field].widget.attrs.update({"autocomplete": "off"})


class OperacionesPTMView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not user_can_access_url_name(request.user, "operaciones_ptm"):
            return HttpResponseForbidden("No tienes permiso para registrar operaciones de caja.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        active = TurnoCaja.objects.filter(cajero=request.user, estado="ABIERTO").order_by("-inicio").first()
        tipo = request.GET.get("tipo", "recarga")
        form = OperacionPTMForm(actor=request.user, initial={
            "turno": active, "tipo": tipo if tipo in {"retiro", "recarga"} else "recarga",
            "solicitud_id": uuid4(),
        })
        return self.render_page(request, form)

    def post(self, request):
        form = OperacionPTMForm(request.POST, actor=request.user)
        if form.is_valid():
            values = form.cleaned_data
            try:
                operation, created = registrar_operacion_ptm(
                    actor=request.user, turno_id=values["turno"].pk, tipo=values["tipo"],
                    monto=values["monto"], referencia=values["referencia"], solicitud_id=values["solicitud_id"],
                )
            except ValidationError as exc:
                form.add_error(None, exc)
            except DatabaseError:
                logging.getLogger(__name__).exception("Fallo al registrar operación PTM")
                form.add_error(None, "No pudimos confirmar el guardado. Revisa el historial; si no aparece, reenvía este mismo formulario para evitar duplicados.")
            else:
                direction = "salida" if operation.tipo == "retiro" else "entrada"
                messages.success(request, f"PTM #{operation.pk}: {direction} de ${operation.monto:,.2f} registrada."
                                 if created else "Esta operación ya estaba guardada. No se registró dos veces.")
                return redirect("operaciones_ptm")
        return self.render_page(request, form, status=400)

    def render_page(self, request, form, status=200):
        can_review = user_can_access_url_name(request.user, "turnos_caja_admin")
        turns = TurnoCaja.objects.all() if can_review else TurnoCaja.objects.filter(cajero=request.user)
        selected_id = request.GET.get("turno", "")
        selected = turns.filter(pk=int(selected_id)).first() if selected_id.isdecimal() else None
        operations = OperacionPTM.objects.select_related("usuario", "turno__puntopago", "producto")
        if not can_review:
            operations = operations.filter(usuario=request.user)
        if selected_id and not selected:
            operations = operations.none()
        elif selected:
            operations = operations.filter(turno=selected)
        query = (request.GET.get("referencia") or "").strip()
        if query:
            operations = operations.filter(referencia__icontains=query)
        page = Paginator(operations, 30).get_page(request.GET.get("page"))
        params = request.GET.copy()
        params.pop("page", None)
        return render(request, "operaciones_ptm.html", {
            "form": form, "page_obj": page, "pagination_query": params.urlencode(),
            "turno_filtro": selected_id, "referencia_filtro": query,
            "resumen": resumen_ptm(selected) if selected else None, "turno_seleccionado": selected,
            "can_review": can_review,
            "conteos": selected.conteos_ptm.select_related("usuario")[:30] if selected and can_review else [],
            "has_open_turn": TurnoCaja.objects.filter(cajero=request.user, estado="ABIERTO").exists(),
        }, status=status)
