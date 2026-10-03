import json
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, transaction
from django.http import JsonResponse, HttpResponseForbidden
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import EquipoHibrido, SesionHibrida, PuntosPago, RecuperacionHibrida
from .permissions import is_web_master_role
from .services import hybrid, hybrid_recovery, hybrid_replica
from .services import hybrid_expenses, hybrid_operations
from pos_shared.protocol import ProtocolError


def api(action, *, enrollment=False):
    @csrf_exempt  # No usa cookies; autorización Bearer por equipo o código temporal.
    @require_POST
    @wraps(action)
    def wrapped(request):
        try:
            if request.content_type != "application/json" or len(request.body) > 2000000:
                raise hybrid.HybridError("Solicitud inválida o demasiado grande.", "request", 400)
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError
            device = None if enrollment else hybrid.authenticate_device(request.headers.get("Authorization", ""))
            if enrollment or action is hybrid.start_session:
                key = "hybrid:attempt:" + hybrid.digest(str(getattr(device, "pk", "")) + request.META.get("REMOTE_ADDR", ""))
                tries = cache.get(key, 0)
                if tries >= 15:
                    raise hybrid.HybridError("Demasiados intentos; espera unos minutos.", "rate_limit", 429)
                cache.set(key, tries + 1, 300)
            result = action(data) if enrollment else action(device, data)
            response = JsonResponse({"ok": True, **result})
        except hybrid.HybridError as exc:
            response = JsonResponse({"ok": False, "code": exc.code, "error": str(exc)}, status=exc.status)
        except (ValueError, TypeError, ProtocolError):
            response = JsonResponse({"ok": False, "code": "invalid", "error": "Revisa los datos de la operación."}, status=400)
        except DatabaseError:
            response = JsonResponse({"ok": False, "code": "unavailable", "error": "El servidor no está disponible; conserva los pendientes y reintenta."}, status=503)
        response["Cache-Control"] = "no-store"
        return response
    return wrapped


enroll = api(hybrid.enroll, enrollment=True)
start_session = api(hybrid.start_session)
catalog = api(hybrid.catalog)
sale = api(hybrid.accept_sale)
expense = api(hybrid_expenses.accept_expense)
operation = api(hybrid_operations.accept_operation)
operation_read = api(hybrid_operations.operation_read)
release_session = api(hybrid.release_session)
recovery_prepare = api(hybrid_recovery.prepare, enrollment=True)
recovery_finish = api(hybrid_recovery.finish, enrollment=True)
replica_prepare = api(hybrid_replica.prepare)
replica_page = api(hybrid_replica.page)


@login_required
def devices(request):
    if not is_web_master_role(request.user):
        return HttpResponseForbidden("Solo Web Master puede vincular equipos.")
    error, code, recovery_code = "", "", ""
    if request.method == "POST":
        if not request.user.check_password(request.POST.get("password", "")):
            error = "Contraseña incorrecta."
        elif request.POST.get("action") in {"recover", "approve_recovery"}:
            try:
                if request.POST["action"] == "recover":
                    _, recovery_code = hybrid_recovery.authorize(actor=request.user,
                        device_id=request.POST.get("device"), reason=request.POST.get("reason", ""),
                        isolated=request.POST.get("isolated") == "on")
                else:
                    hybrid_recovery.approve(actor=request.user, recovery_id=request.POST.get("recovery"),
                        understood=request.POST.get("understood") == "on")
            except hybrid.HybridError as exc:
                error = str(exc)
            except (EquipoHibrido.DoesNotExist, RecuperacionHibrida.DoesNotExist):
                error = "El equipo o la recuperación no existe."
        elif request.POST.get("action") == "revoke":
            try:
                pk = hybrid.identifier(request.POST.get("device"))
                with transaction.atomic():
                    device = EquipoHibrido.objects.select_for_update().get(pk=pk)
                    if SesionHibrida.objects.filter(equipo=device, liberada_en__isnull=True).exists():
                        error = "Primero sincroniza y finaliza la sesión del equipo; puede tener ventas pendientes."
                    else:
                        device.activo = False
                        device.save(update_fields=["activo"])
            except hybrid.HybridError as exc:
                error = str(exc)
            except EquipoHibrido.DoesNotExist:
                error = "El equipo no existe."
        else:
            try:
                name = " ".join(request.POST.get("name", "").split())
                if not 2 <= len(name) <= 80:
                    raise ValueError
                point = PuntosPago.objects.get(pk=int(request.POST.get("point", "")))
                _device, code = hybrid.create_device(actor=request.user, point=point, name=name)
            except (ValueError, PuntosPago.DoesNotExist):
                error = "Escribe un nombre y elige un punto de pago válido."
            except IntegrityError:
                error = "Ese punto ya tiene un equipo activo. No se creó otro."
    response = render(request, "equipos_hibridos.html", {
        "devices": EquipoHibrido.objects.select_related("punto__sucursalid").order_by("nombre"),
        "sessions": SesionHibrida.objects.filter(liberada_en__isnull=True).select_related("equipo", "usuario"),
        "points": PuntosPago.objects.select_related("sucursalid").order_by("nombre"), "error": error, "code": code,
        "recovery_code": recovery_code,
        "recoveries": RecuperacionHibrida.objects.select_related("equipo", "creada_por", "aprobada_por").order_by("-creada_en")[:50],
    }, status=400 if error else 200)
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response
