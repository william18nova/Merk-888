from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse

from .permissions import (
    PUBLIC_URL_NAMES,
    route_permission_for_url_name,
    user_can_access_url_name,
)


class PagePermissionMiddleware:
    """
    Enforces the same app permission catalog used by the navbar.
    Unmapped internal routes stay untouched so existing autocompletes keep working.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = getattr(request, "resolver_match", None)
        url_name = getattr(match, "url_name", None)
        if not url_name or url_name in PUBLIC_URL_NAMES:
            return None

        required_permission = route_permission_for_url_name(url_name)
        if not required_permission:
            return None

        user = getattr(request, "user", None)
        wants_json = (
            request.headers.get("x-requested-with") == "XMLHttpRequest"
            or "application/json" in request.headers.get("accept", "")
        )
        if not getattr(user, "is_authenticated", False):
            if wants_json:
                return JsonResponse({"success": False, "error": "Tu sesion expiro. Vuelve a iniciar sesion."}, status=401)
            return redirect_to_login(request.get_full_path())

        if user_can_access_url_name(user, url_name):
            return None

        message = "No tienes permiso para acceder a esta pagina."
        if wants_json:
            return JsonResponse({"success": False, "error": message}, status=403)

        messages.error(request, message)
        try:
            home_url = reverse("home")
        except NoReverseMatch:
            return HttpResponseForbidden(message)

        if request.path == home_url:
            return HttpResponseForbidden(message)
        return redirect("home")
