"""Cloud-first request boundary. Offline writes remain explicitly gated."""
import logging

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.http import JsonResponse, HttpResponse
from django.urls import resolve, reverse, Resolver404
from django.utils import timezone

from .connectivity import Health, Mode, LocalState, choose_mode
from .cloud_health import monitor
from .models import ReplicaState, LocalCommand
from .request_context import database_context, offline_request

logger = logging.getLogger(__name__)


def unavailable(request, message, code="offline_unavailable"):
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
        response = JsonResponse({"success": False, "error": message, "code": code}, status=503)
    else:
        from django.utils.html import format_html
        response = HttpResponse(format_html('<main lang="es"><h1>Conexión del POS</h1><p>{}</p><p>Conserva la pestaña y el carrito. No se repitió ninguna operación.</p><p>Si tu autorización venció, vuelve a <a href="{}">iniciar sesión cuando haya conexión</a> para revisar los pendientes.</p></main>', message, reverse("login")), status=503)
    response["Cache-Control"] = "no-store"
    # A failed request is not evidence that the fallback is usable. This also
    # lets an already open navbar reflect expiry/conflicts without a reload.
    response["X-Nova-Mode"] = "blocked" if getattr(request, "nova_mode", "") == "blocked" else "unavailable"
    return response


def local_state():
    node_id = settings.LOCAL_CONFIG["instance_id"]
    state = ReplicaState.objects.using("offline").filter(node_id=node_id).first()
    pending = LocalCommand.objects.using("offline").filter(node_id=node_id).exclude(state="accepted")
    return LocalState(prepared=bool(state and state.active_id),
        authorized=bool(state and not state.blocked and state.expires_at and state.expires_at > timezone.now()),
        pending=pending.count(), conflicts=pending.filter(state="conflict").count(),
        refresh_required=bool(state and state.scope.get("refresh_required")))


class ConnectionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        try:
            health = monitor.snapshot()
            decision = choose_mode(health, local_state())
        except DatabaseError:
            return unavailable(request, "La base local no está disponible. No se puede proteger esta sesión.")
        request.nova_mode = decision.mode.value
        try:
            name = resolve(request.path_info).url_name
        except Resolver404:
            name = None
        # A conflict must not trap an expired identity forever. Only login can
        # revalidate in cloud while business operations stay blocked; no queue,
        # catalog expiry, amounts or receipts are changed by this exception.
        if name == "logout" or (name == "login" and health == Health.AVAILABLE):
            alias = "default" if name == "login" else "offline"
            request.nova_mode = "online" if alias == "default" else "offline"
            with database_context(alias):
                response = self.get_response(request)
                response["Cache-Control"] = "no-store"
                response["X-Nova-Mode"] = request.nova_mode
                return response
        # Reading an OWN durable acknowledgement must remain possible while
        # unresolved intents gate new operations. Authentication/permission and
        # ownership are still checked; this route cannot create a sale.
        if getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
            try:
                resolved = resolve(request.path_info).url_name
                confirmation = resolved in {"integrated_sale_confirmation", "integrated_sale_status", "integrated_sync"}
            except Resolver404:
                confirmation = False
            if confirmation and (request.method in {"GET", "HEAD"} or (resolved == "integrated_sync" and request.method == "POST")):
                with database_context("offline"):
                    response = self.get_response(request)
                    response["Cache-Control"] = "no-store"
                    return response
        if decision.mode in {Mode.UNAVAILABLE, Mode.BLOCKED}:
            return unavailable(request, {
                "local_not_prepared": "Aiven no responde y todavía no hay una copia autorizada lista en este equipo.",
                "cloud_rejected": "La nube rechazó la conexión o falta la migración de confirmaciones. Revisa la configuración.",
                "local_authorization_expired": "La autorización offline venció; los datos locales se conservan.",
                "review_required": "Hay operaciones pendientes de revisión. No se descartaron ni se repitieron.",
            }.get(decision.reason, "Estamos verificando la conexión. Inténtalo nuevamente en unos segundos."), decision.reason)
        if decision.mode == Mode.SYNCHRONIZING:
            # Keep the SAME local form usable while the journal worker uploads.
            if not (getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False) and decision.new_operations):
                return unavailable(request, "Estamos conciliando las ventas guardadas. Conserva el carrito.", decision.reason)
        with database_context(decision.database):
            response = self.get_response(request)
            if offline_request() and response.streaming:
                response.close()
                return unavailable(request, "Esta descarga requiere conexión a la nube.")
            response["X-Nova-Mode"] = decision.mode.value
            response["Cache-Control"] = "no-store"
            return response


class OfflineAuthorizationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .integrated_auth import authorized_identity, remember_user
        if not offline_request():
            response = self.get_response(request)
            if request.user.is_authenticated and response.status_code < 400:
                try:
                    remember_user(request.user, force=request.method == "POST" and request.path == "/")
                except (DatabaseError, ValueError):
                    # Do NOT retry a committed cloud sale because its identity
                    # snapshot failed. The original response remains authoritative.
                    response["X-Nova-Offline-Identity"] = "unavailable"
                    logger.warning("POS: no se renovó la identidad local; se conserva la respuesta original.")
            return response
        try:
            name = resolve(request.path_info).url_name
        except Resolver404:
            name = None
        # Local logout always remains possible. Login uses the bounded backend.
        if name in {"login", "logout"}:
            return self.get_response(request)
        grant = authorized_identity(request.user)
        if not grant:
            return unavailable(request, "Este usuario no tiene una autorización offline vigente en este equipo.")
        state = ReplicaState.objects.using("offline").filter(node_id=settings.LOCAL_CONFIG["instance_id"]).first()
        confirmation = (getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False)
            and name in {"integrated_sale_confirmation", "integrated_sale_status", "integrated_sync"} and request.method in {"GET", "HEAD"})
        review_post = (getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False)
            and name == "integrated_sync" and request.method == "POST")
        sale_post = (getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False)
            and name == "generar_venta" and request.method == "POST")
        price_post = (getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False)
            and name == "verificar_producto" and request.method == "POST")
        business_post = (getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False)
            and name in {"registrar_egreso", "ver_venta", "turno_caja_iniciar", "turno_recuperar_o_iniciar", "turno_caja_iniciar_cierre", "turno_caja_cerrar", "turno_caja_cierre_respaldo"}
            and request.method == "POST")
        from .integrated_navigation import read_available, blocked_response
        if not state or state.local_user_id != request.user.pk:
            return unavailable(request, "Este equipo está vinculado a otro usuario. No se modificó su copia local.")
        read_request = request.method in {"GET", "HEAD"}
        # A checkpoint has exactly the existing close-page scope, no new money operation.
        scope_name = "turno_caja_cierre_pagos" if name == "turno_caja_cierre_respaldo" else name
        route_ready = (read_available(scope_name, grant, state) if read_request else
            scope_name in grant.allowed_routes and scope_name in state.scope.get("routes", []))
        if ((not read_request and not sale_post and not price_post and not business_post and not review_post)
                or (not confirmation and not review_post and not route_ready)):
            return blocked_response(request)
        if sale_post or business_post or review_post:
            return self.get_response(request)  # Adapter owns the atomic local transaction.
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
            return self.get_response(request)
