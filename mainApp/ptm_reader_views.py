import json
from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import CuentaLecturaPTM, LectorPTM, MovimientoLeidoPTM, OperacionPTM, LecturaDiariaPTM
from .permissions import user_can_access_url_name
from .services.ptm_reader import BOGOTA, READER_STATES, authenticate_reader, ingest, issue_reader, link


class AccountForm(forms.ModelForm):
    class Meta:
        model = CuentaLecturaPTM
        fields = ["nombre", "usuario_ptm"]
        labels = {"nombre": "Nombre para reconocer esta cuenta", "usuario_ptm": "Usuario exacto de la columna Usuario en PTM"}


class ReaderForm(forms.Form):
    cuenta = forms.ModelChoiceField(queryset=CuentaLecturaPTM.objects.all())
    nombre = forms.CharField(max_length=80, label="Nombre del computador lector")


class ConciliacionPTMView(LoginRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not user_can_access_url_name(request.user, "conciliacion_ptm"):
            return HttpResponseForbidden("No tienes permiso para conciliar PTM.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return self.page(request)

    def post(self, request):
        token = None
        try:
            action = request.POST.get("accion")
            if action == "cuenta":
                form = AccountForm(request.POST)
                if not form.is_valid():
                    raise ValidationError("Revisa la cuenta: nombre y usuario PTM son obligatorios y el usuario no puede repetirse.")
                form.save()
                messages.success(request, "Cuenta de lectura creada. No se modificó la caja.")
            elif action == "lector":
                form = ReaderForm(request.POST)
                if not form.is_valid():
                    raise ValidationError("Selecciona la cuenta y escribe el nombre del lector.")
                _, token = issue_reader(account=form.cleaned_data["cuenta"], name=form.cleaned_data["nombre"], actor=request.user)
            elif action == "revocar":
                LectorPTM.objects.filter(pk=request.POST.get("lector")).update(activo=False)
                messages.success(request, "Lector revocado. Su historial se conserva.")
            elif action in {"vincular", "desvincular"}:
                result = link(movement_id=int(request.POST.get("movimiento", "")),
                    operation_id=int(request.POST.get("operacion") or 0), actor=request.user,
                    reason=request.POST.get("motivo"), unlink=action == "desvincular")
                messages.success(request, "Vínculo actualizado sin mover dinero." + (
                    " Hay diferencias de importe, tipo o estado: revisa el comprobante." if result.conciliacion == "diferencia" else ""))
            else:
                raise ValidationError("Acción no válida.")
        except (ValidationError, ValueError, ObjectDoesNotExist, IntegrityError) as exc:
            messages.error(request, " ".join(exc.messages) if isinstance(exc, ValidationError) else "No se pudo vincular. Revisa los IDs y si ya existe una asociación.")
        if token:
            return self.page(request, token=token)
        return redirect("conciliacion_ptm")

    def page(self, request, token=None):
        day = request.GET.get("fecha", timezone.now().astimezone(BOGOTA).date().isoformat())
        try:
            day_value = date.fromisoformat(day)
        except ValueError:
            day_value = timezone.now().astimezone(BOGOTA).date()
            day = day_value.isoformat()
        queryset = MovimientoLeidoPTM.objects.filter(inicio__date=day_value).select_related(
            "cuenta", "operacion__usuario", "operacion__turno__puntopago")
        account = request.GET.get("cuenta", "")
        if account.isdecimal():
            queryset = queryset.filter(cuenta_id=int(account))
        totals = dict(queryset.values_list("conciliacion").annotate(n=Count("pk")))
        state = request.GET.get("estado", "")
        if state in {"sin_vincular", "vinculado", "diferencia", "revisar"}:
            queryset = queryset.filter(conciliacion=state)
        page = Paginator(queryset, 30).get_page(request.GET.get("page"))
        for movement in page:
            movement.label = {"sin_vincular": "Sin vincular", "vinculado": "Vinculado", "diferencia": "Con diferencias", "revisar": "Por revisar"}.get(movement.conciliacion, "Por revisar")
            movement.events = movement.auditoria.select_related("usuario").order_by("-creado_en")[:20]
            movement.candidates = OperacionPTM.objects.filter(
                Q(referencia=movement.id_tx) | Q(referencia=movement.id_operador)).select_related("usuario", "turno")[:5]
        readers = list(LectorPTM.objects.select_related("cuenta").order_by("nombre"))
        for reader in readers:
            reader.label = {"pendiente": "Pendiente de activación", "ok": "Reporte recibido", "sesion": "Inicia sesión en PTM", "estructura": "Cambió el formato del reporte", "conexion": "Problema de conexión", "parcial": "Lectura parcial", "limite": "Revisar volumen del reporte", "cuenta": "Revisar cuenta configurada"}.get(reader.estado, "Revisar lector")
            reader.stale = (not reader.ultima_lectura or reader.ultima_lectura < timezone.now() - timedelta(minutes=10)
                            or reader.fecha_reporte != timezone.now().astimezone(BOGOTA).date())
        params = request.GET.copy()
        params.pop("page", None)
        # Observaciones del portal y operaciones del POS son registros separados.
        unmatched = OperacionPTM.objects.filter(creado_en__date=day_value, lectura_ptm__isnull=True).select_related("usuario", "turno")[:100]
        response = render(request, "conciliacion_ptm.html", dict(page_obj=page, totals=totals, lectores=readers,
            cuentas=CuentaLecturaPTM.objects.all(), fecha=day, estado=state, cuenta=account,
            account_form=AccountForm(), reader_form=ReaderForm(), token=token,
            pagination_query=params.urlencode(), operaciones_pendientes=unmatched))
        response["Cache-Control"] = "no-store"
        # Native form POSTs under no-referrer can send Origin: null, which
        # Django correctly rejects. Keep the origin for our own forms without
        # sending a referrer to other sites. Reader secrets never enter URLs.
        response["Referrer-Policy"] = "same-origin"
        return response


@csrf_exempt  # Token de lector limitado; nunca acepta autenticación por cookie.
@require_POST
def receive_ptm(request):
    def reply(data, status=200):
        response = JsonResponse(data, status=status)
        response["Cache-Control"] = "no-store"
        return response
    auth = request.headers.get("Authorization", "")
    if len(auth) > 180 or not auth.startswith("Bearer "):
        return reply({"error": "Lector no autorizado."}, 401)
    if request.content_type != "application/json":
        return reply({"error": "Se requiere JSON."}, 415)
    try:
        length = int(request.META.get("CONTENT_LENGTH") or 0)
    except (ValueError, TypeError):
        return reply({"error": "Longitud no válida."}, 400)
    if length > 131072:
        return reply({"error": "Lote demasiado grande."}, 413)
    try:
        reader = authenticate_reader(auth[7:])
        if not reader:
            return reply({"error": "Lector revocado, vencido o no autorizado."}, 401)
        if len(request.body) > 131072:
            return reply({"error": "Lote demasiado grande."}, 413)
        payload = json.loads(request.body)
        if not isinstance(payload, dict):
            raise ValidationError("Solicitud inválida.")
        if payload.get("accion") == "estado":
            state = payload.get("estado")
            if not isinstance(state, str) or state not in READER_STATES:
                raise ValidationError("Estado del lector inválido.")
            reader.estado = state
            reader.ultimo_contacto = timezone.now()
            if state == "ok":
                try:
                    report_date = date.fromisoformat(payload.get("fecha", ""))
                except (ValueError, TypeError):
                    raise ValidationError("Falta la fecha de la lectura completa.")
                if report_date > timezone.now().astimezone(BOGOTA).date():
                    raise ValidationError("Fecha futura no válida.")
                reader.ultima_lectura = timezone.now()
                reader.fecha_reporte = report_date
            _save_reader_status(reader)
            return reply({"ok": True})
        result = ingest(reader, payload)
        return reply({"ok": True, **result})
    except (ValidationError, ValueError, UnicodeDecodeError) as exc:
        return reply({"error": " ".join(exc.messages) if isinstance(exc, ValidationError) else "JSON inválido."}, 400)
    except DatabaseError:
        # No exponer consultas/credenciales ni confirmar una escritura incierta.
        return reply({"error": "No se confirmó el guardado. El lector debe conservar y reenviar el lote."}, 503)


@transaction.atomic
def _save_reader_status(reader):
    # A completed report and its daily coverage commit together, serialized
    # with ingestion and administrative reviews of this same account.
    CuentaLecturaPTM.objects.select_for_update().get(pk=reader.cuenta_id)
    reader.save(update_fields=["estado", "ultimo_contacto", "ultima_lectura", "fecha_reporte"])
    if reader.estado == "ok":
        LecturaDiariaPTM.objects.update_or_create(cuenta=reader.cuenta, fecha=reader.fecha_reporte,
            defaults={"completada_en": reader.ultima_lectura})
