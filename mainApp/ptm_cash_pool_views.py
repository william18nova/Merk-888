from decimal import Decimal
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from .models import BolsaEfectivoPTM, CorteBolsaPTM, CuentaLecturaPTM, Sucursal, TurnoCaja
from .services import ptm_cash_pool as pool


class SetupForm(forms.Form):
    sucursal = forms.ModelChoiceField(queryset=Sucursal.objects.all())
    cuenta = forms.ModelChoiceField(queryset=CuentaLecturaPTM.objects.all(), label="Cuenta PTM exclusiva de esta sucursal")
    base = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), label="Efectivo real inicial de la bolsa (COP)")
    confirma = forms.BooleanField(label="Detuvimos las operaciones PTM, conté la bolsa completa y está separada de las cajas. Esta cuenta PTM solo se usa en esta sucursal.")


class CountForm(forms.Form):
    contado = forms.DecimalField(max_digits=14, decimal_places=2, min_value=Decimal("0"), label="Efectivo contado en toda la bolsa PTM (COP)")
    token = forms.CharField(widget=forms.HiddenInput)
    confirma = forms.BooleanField(label="Detuvimos las operaciones PTM mientras contamos toda la bolsa. Confirmo el importe; no podré editarlo.")


class BolsaPTMAdminView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not pool.is_admin(request.user):
            return HttpResponseForbidden("Esta página es exclusiva para Web Master.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return self.page(request, SetupForm())

    def post(self, request):
        form = SetupForm()
        try:
            action = request.POST.get("accion")
            if action == "crear":
                form = SetupForm(request.POST)
                if not form.is_valid():
                    return self.page(request, form, status=400)
                pool.configure(actor=request.user, branch=form.cleaned_data["sucursal"],
                    account=form.cleaned_data["cuenta"], base=form.cleaned_data["base"])
                messages.success(request, "Bolsa creada. Cada cajero debe contar y recibirla desde su turno. No se cambiaron saldos de cajas.")
            elif action == "ajuste":
                from uuid import UUID
                pool.adjust(actor=request.user, bag=get_object_or_404(BolsaEfectivoPTM, pk=request.POST.get("bolsa")),
                    amount=request.POST.get("monto"), reason=request.POST.get("motivo"), request_id=UUID(request.POST.get("solicitud", "")))
                messages.success(request, "Entrada/salida de bolsa registrada con trazabilidad.")
            elif action == "revisar":
                pool.review(actor=request.user, cut_id=int(request.POST.get("corte", "")), reason=request.POST.get("motivo"))
                messages.success(request, "Corte revisado. No se hicieron cobros ni descuentos automáticos.")
            else:
                raise ValidationError("Acción inválida.")
        except (ValidationError, ValueError, IntegrityError, CorteBolsaPTM.DoesNotExist) as exc:
            messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else "Revisa los datos; la sucursal y cuenta no pueden repetirse.")
            return self.page(request, form, status=400)
        return redirect("bolsa_ptm_admin")

    def page(self, request, form, status=200):
        bags = list(BolsaEfectivoPTM.objects.select_related("sucursal", "cuenta"))
        for bag in bags:
            bag.request_id = uuid4()
        cuts = CorteBolsaPTM.objects.select_related("bolsa__sucursal", "turno__cajero").prefetch_related("participantes__cajero", "auditoriabolsaptm_set").order_by("-hasta")[:100]
        response = render(request, "bolsa_ptm_admin.html", {"form": form, "bolsas": bags, "cortes": cuts}, status=status)
        response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = "same-origin"
        return response


class BolsaPTMTurnoView(LoginRequiredMixin, View):
    def get_turn(self, request, turno_id):
        return get_object_or_404(TurnoCaja.objects.select_related("puntopago__sucursalid", "cajero"), pk=turno_id, cajero=request.user)

    def get(self, request, turno_id):
        turn = self.get_turn(request, turno_id)
        try:
            token = pool.ticket(turn, request.user)
        except ValidationError as exc:
            messages.warning(request, " ".join(exc.messages))
            return redirect("turno_caja")
        return self.page(request, turn, CountForm(initial={"token": token}))

    def post(self, request, turno_id):
        turn = self.get_turn(request, turno_id)
        form = CountForm(request.POST)
        if form.is_valid():
            try:
                pool.count(actor=request.user, turn_id=turn.pk, counted=form.cleaned_data["contado"], token=form.cleaned_data["token"])
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                messages.success(request, "Conteo PTM registrado.")
                if turn.estado == "CIERRE":
                    return redirect("turno_caja_cierre_pagos", turno_id=turn.pk)
                return redirect("turno_caja")
        return self.page(request, turn, form, status=400)

    def page(self, request, turn, form, status=200):
        data = pool.payload(turn)
        response = render(request, "bolsa_ptm_conteo.html", {"form": form, "turno": turn,
            "bolsa_ptm": data, "entrega": data.get("participa", False)}, status=status)
        response["Cache-Control"] = "no-store, private"
        response["Referrer-Policy"] = "same-origin"
        return response
