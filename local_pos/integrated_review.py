"""Audited revalidation of immutable events; never force a financial receipt."""
from uuid import uuid4, UUID
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.urls import path
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods
from mainApp.permissions import is_web_master_role, user_has_permission
from .integrated_auth import cloud_fingerprint, authorized_identity
from .integrated_journal import operation_lock
from .models import LocalNode, LocalCommand, ReplicaState


def manager(actor):
    return actor.is_active and (is_web_master_role(actor) or user_has_permission(actor, "caja_turnos_editar"))


def retry(actor, operation_id, token, note):
    if not manager(actor) or not authorized_identity(actor):
        raise PermissionDenied
    token = str(UUID(str(token)))
    note = str(note).strip()
    if not 8 <= len(note) <= 400:
        raise ValueError("Explica lo que revisaste (entre 8 y 400 caracteres).")
    with operation_lock(operation_id) as locked:
        if not locked:
            raise ValueError("La operación ya está siendo revisada. Espera un momento.")
        with transaction.atomic(using="offline"):
            node = LocalNode.objects.using("offline").select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
            state = ReplicaState.objects.using("offline").get(node=node)
            row = LocalCommand.objects.using("offline").get(pk=operation_id, node=node)
            if state.local_user_id != actor.pk or row.payload.get("source") != cloud_fingerprint():
                raise PermissionDenied
            history = row.local_result.get("review_history", [])
            if any(event["request_id"] == token for event in history):
                return
            first = LocalCommand.objects.using("offline").filter(node=node).exclude(state="accepted").order_by("sequence").first()
            if not first or first.pk != row.pk or row.state != "conflict":
                raise ValueError("Solo puedes revisar la primera operación pendiente; actualiza la página.")
            if len(history) >= 100:
                raise ValueError("Se alcanzó el límite de reintentos. Conserva el historial para conciliación técnica.")
            row.local_result = {**row.local_result, "review_history": [*history, {"request_id": token,
                "at": timezone.now().isoformat(), "actor_id": actor.pk, "note": note,
                "previous_reason": row.local_result.get("reason", "")}]}
            row.state = "pending"
            row.save(using="offline", update_fields=["local_result", "state"])


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def index(request):
    if not getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
        raise PermissionDenied
    state = ReplicaState.objects.using("offline").filter(node_id=settings.LOCAL_CONFIG["instance_id"],
        local_user_id=request.user.pk, server="django-db:" + cloud_fingerprint()).first()
    if not state or not request.user.is_active:
        raise PermissionDenied
    error = ""
    if request.method == "POST":
        try:
            retry(request.user, UUID(request.POST.get("operation_id", "")), request.POST.get("request_id"), request.POST.get("note"))
            return redirect("integrated_sync")
        except (ValueError, LocalCommand.DoesNotExist) as exc:
            error = str(exc) if isinstance(exc, ValueError) else "La operación ya cambió. Actualiza la pantalla."
    commands = LocalCommand.objects.using("offline").filter(node=state.node, actor=request.user)
    labels = {"expense": "Pago", "refund": "Devolución", "open": "Apertura de turno", "close_begin": "Inicio del cierre", "close": "Cierre"}
    page = Paginator(commands.order_by("-sequence"), 30).get_page(request.GET.get("page"))
    first = commands.exclude(state="accepted").order_by("sequence").first()
    items = []
    for command in page:
        result = command.local_result
        resume = result.get("response", {}).get("redirect", "")
        if (not isinstance(resume, str) or not resume.startswith("/") or resume.startswith("//")
                or command.state == "conflict" or result.get("outcome") not in {"confirmed", "saved_offline", "synchronized"}):
            resume = ""
        items.append({"id": str(command.pk), "sequence": command.sequence, "kind": labels.get(command.payload.get("action"), "Venta"),
            "date": command.created_at, "state": command.state,
            "label": ("No registrada en la nube" if command.state == "accepted" and result.get("outcome") in {"absent", "rejected"}
                else {"pending": "Pendiente de envío", "intent": "Verificando respuesta", "accepted": "Conciliada", "conflict": "Requiere revisión"}.get(command.state, command.state)),
            "reason": result.get("reason", ""), "history": result.get("review_history", []),
            "resume": resume,
            "can_retry": bool(manager(request.user) and first and first.pk == command.pk and command.state == "conflict"),
            "cloud": result.get("cloud_response", {}).get("venta_id") or result.get("cloud_response", {}).get("expense_id"),
            "closed_turn": result.get("cloud_response", {}).get("turno_id") if command.payload.get("action") == "close" and command.state == "accepted" else None,
            "total": command.payload.get("total") or command.payload.get("values", {}).get("total")})
    if request.GET.get("format") == "json":
        return JsonResponse({"pending": commands.exclude(state="accepted").count(), "operations": items})
    return render(request, "local_pos/integrated_review.html", {"operations": items, "page_obj": page,
        "retry_token": str(uuid4()), "error": error, "pending": commands.exclude(state="accepted").count()})


urlpatterns = [path("local/sincronizacion/", index, name="integrated_sync")]
