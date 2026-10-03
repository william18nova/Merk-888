"""Cinturón de seguridad temporal del laboratorio Django completo."""
from urllib.parse import urlsplit
import re

from django.conf import settings
from django.db import connection, transaction
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import Resolver404, resolve

from .capabilities import AUTH_ROUTES, READ_ROUTES


class LocalSafetyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def unavailable(request, reason):
        message = ("Esta operación todavía no está habilitada en el laboratorio local. "
                   "No se guardaron cambios ni se enviaron a la nube.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            response = JsonResponse({"success": False, "error": message, "code": reason}, status=409)
        else:
            response = render(request, "local_pos/unavailable.html", {"local_reason": reason, "local_message": message}, status=409)
        response["Cache-Control"] = "no-store"
        return response

    def __call__(self, request):
        try:
            name = resolve(request.path_info).url_name
        except Resolver404:
            name = None
        sale_route = (getattr(settings, "HYBRID_LOCAL_SALES_ENABLED", False)
                      and name in {"generar_venta", "local_sale_api", "local_sale_lookup", "local_sale_asset"})
        sale_route = sale_route or (getattr(settings, "HYBRID_LOCAL_OPERATIONS_ENABLED", False)
                                    and name in {"registrar_egreso", "local_return", "turno_caja"})
        # La revisión debe seguir disponible incluso con autorización vencida.
        # Estas vistas controlan alcance/autor, permisos y POST con CSRF.
        sale_route = sale_route or name in {"local_sync", "local_sync_detail", "local_sync_send", "local_sync_retry"}
        if not sale_route and name not in AUTH_ROUTES and name != "local_runtime_status":
            from .models import ReplicaState
            from django.utils import timezone
            state = ReplicaState.objects.filter(node_id=settings.LOCAL_CONFIG["instance_id"]).first()
            if state and (state.blocked or not state.active_id or not state.expires_at
                          or state.expires_at <= timezone.now()
                          or getattr(request.user, "pk", None) != state.local_user_id
                          or name not in state.scope.get("routes", [])):
                return self.unavailable(request, "replica_not_authorized")
        if name in AUTH_ROUTES or sale_route:
            # También proteger acciones exentas de CSRF añadidas en el futuro.
            origin = request.headers.get("Origin")
            if request.method not in {"GET", "HEAD"} and origin:
                parsed = urlsplit(origin)
                if parsed.scheme != request.scheme or parsed.netloc != request.get_host():
                    return self.unavailable(request, "cross_origin")
            response = self.get_response(request)
        elif name not in READ_ROUTES or request.method not in {"GET", "HEAD"}:
            response = self.unavailable(request, "sync_not_implemented")
        else:
            rejected_write = False

            def detect_write(execute, sql, params, many, context):
                nonlocal rejected_write
                try:
                    return execute(sql, params, many, context)
                except Exception as exc:
                    cause = getattr(exc, "__cause__", None)
                    if getattr(cause, "pgcode", None) == "25006":
                        rejected_write = True
                    raise

            # La transacción incluye middleware de permisos, vista y renderizado.
            # Las sesiones quedan fuera; se permite iniciar/cerrar sesión local.
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                with connection.execute_wrapper(detect_write):
                    response = self.get_response(request)
                if rejected_write:
                    transaction.set_rollback(True)
            if rejected_write:
                response = self.unavailable(request, "read_view_requires_adapter")
        if not response.streaming and response.get("Content-Type", "").startswith("text/html"):
            html = response.content.decode(response.charset)
            for remote, local in sorted(getattr(settings, "LOCAL_ASSET_MAP", {}).items(), key=lambda item: -len(item[0])):
                html = html.replace(remote, local)
            html = re.sub(r'<link\b[^>]*href=[\'\"]https://fonts\.googleapis\.com/[^>]*>', '', html)
            response.content = html.encode(response.charset)
            if response.has_header("Content-Length"):
                response["Content-Length"] = str(len(response.content))
        response["X-Nova-Mode"] = "development-readonly"
        response["Cache-Control"] = "no-store"
        # Sin dependencias de internet, agentes de impresión ni conexiones remotas.
        response["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "font-src 'self'; connect-src 'self'; frame-src 'none'; "
            "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        return response
