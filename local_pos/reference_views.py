from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.db.models import Count
from django.shortcuts import render, redirect
from django.urls import path
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from mainApp.permissions import _load_permission_state, user_can_access_url_name
from mainApp.models import PuntosPago
from .models import ReplicaState, ReplicaRow
from .reference_runtime import worker
from .request_context import offline_request
from .integrated_auth import cloud_fingerprint, authorized_identity
from .integrated_replica import SOURCE_PREFIX


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def index(request):
    if not getattr(settings, "NOVA_REFERENCE_ENABLED", False):
        raise Http404
    _load_permission_state(request.user, fresh=True)
    employee = getattr(request.user, "empleado", None)
    if not request.user.is_active or not employee or not employee.sucursalid_id or not user_can_access_url_name(request.user, "generar_venta"):
        return JsonResponse({"error": "Necesitas permiso para vender y una sucursal asignada."}, status=403)
    node_id = settings.LOCAL_CONFIG["instance_id"]
    state = ReplicaState.objects.using("offline").filter(node_id=node_id).first()
    own = state is None or state.local_user_id == request.user.pk
    points = PuntosPago.objects.filter(sucursalid_id=employee.sucursalid_id).order_by("nombre")
    if request.method == "POST":
        if offline_request():
            return JsonResponse({"error": "Para actualizar la copia necesitas conexión."}, status=503)
        try:
            point = int(request.POST.get("point_id", ""))
        except (TypeError, ValueError):
            return JsonResponse({"error": "Selecciona una caja válida."}, status=400)
        if not points.filter(pk=point).exists():
            return JsonResponse({"error": "No tienes autorización para esa caja."}, status=403)
        if not own or (state and state.scope.get("point_id") != point):
            return JsonResponse({"error": "El equipo ya está vinculado a otro usuario o caja. No se reemplazó su copia."}, status=409)
        worker.request(request.user.pk, point)
        return redirect("integrated_reference")
    status = worker.status()
    binding_valid = bool(state and state.server == SOURCE_PREFIX + cloud_fingerprint()
        and str(state.device_id) == str(node_id) and state.scope.get("branch_id") == employee.sucursalid_id
        and points.filter(pk=state.scope.get("point_id")).exists())
    ready = bool(own and binding_valid and state.active_id and not state.blocked and state.expires_at
        and state.expires_at > timezone.now() and authorized_identity(request.user))
    if own and state and not status["busy"] and status["message"] == "Prepara la copia inicial para este usuario y caja.":
        status["message"] = state.status
    if own and state and not binding_valid:
        status["message"] = "La vinculación a nube, sucursal o caja cambió. Conservamos la copia; necesita revisión."
    counts = dict(ReplicaRow.objects.using("offline").filter(node_id=node_id)
        .values("entity").annotate(total=Count("pk")).values_list("entity", "total")) if own and state else {}
    if request.GET.get("format") == "json":
        return JsonResponse({"ready": ready, "busy": status["busy"], "message": status["message"] if own else "Equipo vinculado a otro usuario.",
            "counts": counts, "has_copy": bool(own and state and state.active_id),
            "synchronized_at": state.synchronized_at if own and state else None,
            "expires_at": state.expires_at if own and state else None})
    labels = {"branch": "Sucursales", "point": "Cajas", "category": "Categorías", "product": "Productos",
        "stock": "Registros de inventario", "client": "Clientes", "payment_method": "Medios de pago"}
    return render(request, "local_pos/reference.html", {"copy_state": state if own else None,
        "journal_enabled": getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False),
        "copy_ready": ready, "copy_status": status if own else {"message": "Este equipo está vinculado a otro usuario; se conserva su copia."},
        "points": points, "copy_own": own, "copy_counts": [(key, label, counts.get(key, 0)) for key, label in labels.items()]})


urlpatterns = [path("local/copia/", index, name="integrated_reference")]
