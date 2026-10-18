"""Offline UI navigation, without executing unsupported business views."""
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import Resolver404, path, resolve
from django.views.decorators.http import require_GET

from .request_context import offline_request

# These read-only UI endpoints do not need additional catalog rows. Business
# routes still require BOTH the identity grant and the snapshot scope.
UI_READ_ROUTES = {"home", "integrated_navigation", "integrated_reference", "database_connection_status"}
CATALOG_VIEW_ROUTES = {"visor_cajero", "visor_cajero_buscar", "visor_barcode",
    "visor_barcode_buscar", "visor_barcode_lookup"}
OFFLINE_MESSAGE = "Estamos offline. Esta opción necesita conexión a internet. Puedes seguir trabajando en las funciones disponibles."


def wants_json(request):
    return (request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in request.headers.get("Accept", "")
            or "application/json" in request.headers.get("Content-Type", "")
            or request.headers.get("Sec-Fetch-Dest") == "empty")


def read_available(name, grant, state):
    if name in UI_READ_ROUTES:
        return True
    if (getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False)
            and name in {"integrated_sync", "integrated_sale_confirmation", "integrated_sale_status"}):
        return True
    if name in CATALOG_VIEW_ROUTES:
        # Same price/name/barcode data already granted by this catalog route.
        # No new snapshot, historical report or anonymous access is authorized.
        name = "producto_autocomplete_global"
    return name in grant.allowed_routes and name in state.scope.get("routes", [])


def blocked_response(request):
    """A direct URL keeps the original navbar. AJAX stays a failed operation."""
    from mainApp.permissions import build_nav_menu
    if wants_json(request):
        response = JsonResponse({"success": False, "error": OFFLINE_MESSAGE,
            "code": "offline_route_not_ready"}, status=503)
    else:
        title = "Esta opción requiere conexión"
        for item in build_nav_menu(request.user, request.path):
            for entry in item.get("children") or [item]:
                if urlsplit(entry["url"]).path == request.path:
                    title = entry["label"]
        response = render(request, "local_pos/offline_unavailable.html", {
            "offline_page_title": title, "offline_message": OFFLINE_MESSAGE,
        }, status=200 if request.method in {"GET", "HEAD"} else 503)
    response["X-Nova-Offline-Blocked"] = "1"
    response["Cache-Control"] = "no-store"
    return response


@require_GET
@login_required
def availability(request):
    """Read-only preflight for navigation, not authority to execute an action."""
    from mainApp.permissions import user_can_access_url_name
    from .integrated_auth import authorized_identity
    from .models import ReplicaState
    target = urlsplit(request.GET.get("path", "/home/"))
    if target.scheme or target.netloc or not target.path.startswith("/") or "\\" in target.path:
        return JsonResponse({"allowed": False}, status=400)
    try:
        name = resolve(target.path).url_name
    except Resolver404:
        return JsonResponse({"allowed": False, "message": "Esta página no existe."}, status=404)
    if not user_can_access_url_name(request.user, name):
        return JsonResponse({"allowed": False, "message": "No tienes permiso para esta opción."}, status=403)
    allowed = True
    offline = offline_request()
    if offline:
        grant = authorized_identity(request.user)
        state = ReplicaState.objects.using("offline").filter(node_id=settings.LOCAL_CONFIG["instance_id"]).first()
        allowed = bool(grant and state and state.local_user_id == request.user.pk and read_available(name, grant, state))
    return JsonResponse({"allowed": allowed, "offline": offline,
        "message": "" if allowed else OFFLINE_MESSAGE})


urlpatterns = [path("local/disponibilidad/", availability, name="integrated_navigation")]
