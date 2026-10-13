from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.urls import path
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from mainApp.permissions import user_can_access_url_name
from .integrated_auth import cloud_fingerprint
from .integrated_journal import KIND
from .integrated_offline_sales import KIND as OFFLINE_KIND
from .models import LocalCommand
from .models import ReplicaState


@never_cache
@login_required
@require_GET
def confirmation(request, operation_id):
    if not getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
        raise Http404
    if not request.user.is_active or not user_can_access_url_name(request.user, "generar_venta"):
        return JsonResponse({"status": "denied"}, status=403)
    command = LocalCommand.objects.using("offline").filter(pk=operation_id,
        node_id=settings.LOCAL_CONFIG["instance_id"], actor_id=request.user.pk, kind__in=[KIND, OFFLINE_KIND]).first()
    if not command or command.payload.get("source") != cloud_fingerprint():
        raise Http404
    if command.kind == OFFLINE_KIND:
        return JsonResponse({"status": "saved_offline", "response": command.local_result["response"],
            "synchronization": command.state,
            "cloud_sale_id": command.local_result.get("cloud_response", {}).get("venta_id")})
    outcome = command.local_result.get("outcome")
    if command.state == "accepted" and outcome == "confirmed":
        return JsonResponse({"status": "confirmed", "response": command.local_result["response"]})
    if command.state == "accepted" and outcome in {"absent", "rejected"}:
        return JsonResponse({"status": "not_confirmed"})
    return JsonResponse({"status": "review" if command.state == "conflict" else "pending"})


@never_cache
@login_required
@require_GET
def status(request):
    if not getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
        raise Http404
    if not user_can_access_url_name(request.user, "generar_venta"):
        return JsonResponse({"status": "denied"}, status=403)
    state = ReplicaState.objects.using("offline").filter(node_id=settings.LOCAL_CONFIG["instance_id"],
        local_user_id=request.user.pk, server="django-db:" + cloud_fingerprint()).first()
    if not state:
        return JsonResponse({"mode": "unavailable", "pending": 0, "review": 0, "sales": []})
    commands = LocalCommand.objects.using("offline").filter(node=state.node, actor=request.user, kind=OFFLINE_KIND)
    return JsonResponse({"mode": getattr(request, "nova_mode", "unknown"),
        "pending": commands.filter(state__in=["intent", "pending"]).count(),
        "review": commands.filter(state="conflict").count(), "sales": [
            {"reference": str(row.pk), "state": row.state, "total": row.payload["total"],
                "cloud_sale_id": row.local_result.get("cloud_response", {}).get("venta_id"),
                "reason": row.local_result.get("reason", "")}
            for row in commands.order_by("-sequence")[:10]]})


urlpatterns = [path("local/ventas/confirmacion/<uuid:operation_id>/", confirmation, name="integrated_sale_confirmation"),
    path("local/ventas/estado/", status, name="integrated_sale_status")]
