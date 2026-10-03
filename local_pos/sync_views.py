"""Pantalla local: proyección segura del diario, sin exponer contratos firmados."""
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET, require_POST

from pos_shared.protocol import ProtocolError
from . import sync_review as review
from .sale_views import remote


class Filters(forms.Form):
    state = forms.ChoiceField(required=False, choices=[("", "Todos los estados"), ("waiting", "Sin confirmar"), *review.STATES.items()])
    kind = forms.ChoiceField(required=False, choices=[("", "Todos los movimientos"), *review.KINDS.items()])
    q = forms.CharField(required=False, max_length=80, label="Referencia o número")
    since = forms.DateField(required=False, label="Desde", widget=forms.DateInput(attrs={"type": "date"}))
    until = forms.DateField(required=False, label="Hasta", widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        if data.get("since") and data.get("until") and data["since"] > data["until"]:
            raise forms.ValidationError("La fecha inicial no puede ser posterior a la final.")
        return data


def money(value):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or abs(number) > Decimal("1e18"):
            return "—"
        return "$ " + format(number, ",.2f").replace(",", "_").replace(".", ",").replace("_", ".")
    except (InvalidOperation, ValueError, TypeError):
        return "—"


def presentation(row):
    result = row.local_result
    code = result.get("sync", {}).get("code", "unknown")
    reason, advice = review.REASONS.get(code, review.REASONS["unknown"])
    if row.state == "accepted":
        reason, advice = "La nube confirmó este movimiento.", "No debes registrarlo de nuevo."
    elif row.state == "intent":
        reason, advice = "La intención está guardada en este equipo.", "Se enviará en orden, con la misma referencia."
    elif row.state == "pending" and code == "unknown":
        reason, advice = "Guardada, a la espera de confirmación.", "Mantén abierto el POS para que continúe la sincronización."
    return {"id": row.pk, "number": row.sequence, "kind": review.KINDS.get(row.kind, "Operación no reconocida"),
            "state": row.state, "state_label": review.STATES.get(row.state, "Por revisar"),
            "actor": row.actor.get_username(), "created_at": row.created_at,
            "total": money(result.get("total", result.get("returned_total"))),
            "concept": result.get("concept", ""), "reason": reason, "advice": advice,
            "cloud_id": result.get("sale_id") or result.get("expense_id") or result.get("turn_id"),
            "attempts": result.get("sync", {}).get("attempts", 0),
            "last_attempt": parse_datetime(result.get("sync", {}).get("last_attempt") or ""),
            "history": [{**event, "at": parse_datetime(event.get("at", ""))}
                        for event in reversed(result.get("review_history", []))],
            "code": code if code in review.REASONS else "unknown"}


@login_required
@require_GET
def index(request):
    commands = review.visible_commands(request.user)
    totals = dict(commands.values_list("state").annotate(n=Count("pk")))
    filters = Filters(request.GET)
    rows = commands
    if filters.is_valid():
        data = filters.cleaned_data
        if data.get("state") == "waiting":
            rows = rows.exclude(state="accepted")
        elif data.get("state"):
            rows = rows.filter(state=data["state"])
        if data.get("kind"):
            rows = rows.filter(kind=data["kind"])
        if data.get("q"):
            q = data["q"].strip().lstrip("#")
            condition = Q(operation_id__icontains=q)
            if q.isdigit() and len(q) < 19:
                condition |= Q(sequence=int(q))
            rows = rows.filter(condition)
        if data.get("since"):
            rows = rows.filter(created_at__date__gte=data["since"])
        if data.get("until"):
            rows = rows.filter(created_at__date__lte=data["until"])
    else:
        rows = rows.none()
    page = Paginator(rows.select_related("actor").order_by("-sequence"), 20).get_page(request.GET.get("page"))
    state, session = review.connection_context(request.user)
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "local_pos/sync.html", {
        "filters": filters, "rows": [presentation(row) for row in page], "page": page, "filter_query": query.urlencode(),
        "counts": {"pending": totals.get("intent", 0)+totals.get("pending", 0),
                   "conflicts": totals.get("conflict", 0), "accepted": totals.get("accepted", 0), "total": sum(totals.values())},
        "state": state, "session": session, "is_manager": review.manager(request.user),
        "expired": bool(state and (not state.expires_at or state.expires_at <= timezone.now())),
    })


@login_required
@require_GET
def detail(request, operation_id):
    command = get_object_or_404(review.visible_commands(request.user).select_related("actor"), pk=operation_id)
    state, session = review.connection_context(request.user)
    first = review.visible_commands(request.user).exclude(state="accepted").order_by("sequence").first()
    can_retry = bool(review.manager(request.user) and state and session and first and first.pk == command.pk
        and command.state == "conflict" and command.kind in review.KINDS
        and command.payload.get("session_id") == str(session.session_id))
    # No enviar payload, firmas de precio, autorizaciones o respuestas remotas al HTML.
    payload = command.payload
    data = payload.get("data", {})
    lines = []
    if command.kind == "sale.cash.v1":
        from mainApp.models import Producto
        items = payload.get("items", [])[:100]
        names = dict(Producto.objects.filter(pk__in=[item.get("id") for item in items]).values_list("pk", "nombre"))
        lines = [{"label": names.get(item.get("id"), "Producto #"+str(item.get("id"))), "value": item.get("quantity")} for item in items]
    elif command.kind == "return.v1":
        lines = [{"label": "Venta original", "value": data.get("sale_id")},
                 {"label": "Reintegro solicitado", "value": money(data.get("expected_total"))}]
    elif command.kind == "turn.close.v1":
        lines = [{"label": "Turno", "value": data.get("turn_id")},
                 {"label": "Facturas / compañeros", "value": money(data.get("bills_paid"))},
                 {"label": "Efectivo contado", "value": money(data.get("cash_counted"))}]
    elif command.kind == "expense.create.v1":
        lines = [{"label": "Medio de pago", "value": command.local_result.get("method")},
                 {"label": "Base", "value": money(command.local_result.get("base"))},
                 {"label": "Impuesto", "value": money(command.local_result.get("tax"))}]
    return render(request, "local_pos/sync_detail.html", {"row": presentation(command), "lines": lines,
        "can_retry": can_retry, "is_manager": review.manager(request.user), "request_id": uuid4(),
        "first_other": first if first and first.pk != command.pk else None})


def action_error(request, exc):
    # Nunca devolver un traceback, mensaje HTTP remoto o datos de conexión.
    message = str(exc) if isinstance(exc, ProtocolError) else "No se completó el intento. Los pendientes siguen guardados; espera unos segundos y actualiza."
    messages.error(request, message)


@login_required
@require_POST
def retry(request, operation_id):
    get_object_or_404(review.visible_commands(request.user), pk=operation_id)
    try:
        state = review.retry_conflict(request.user, operation_id, request_id=request.POST.get("request_id"),
                                      note=request.POST.get("note", ""), remote_factory=remote)
        if state == "accepted":
            messages.success(request, "La nube confirmó la operación. No se duplicó el movimiento.")
        else:
            messages.warning(request, "La operación sigue sin confirmar. Revisa su estado y la causa; no repitas el cobro.")
    except (ProtocolError, ValueError, OSError, DatabaseError) as exc:
        action_error(request, exc)
    return redirect("local_sync_detail", operation_id=operation_id)


@login_required
@require_POST
def synchronize(request):
    try:
        review.synchronize_pending(request.user, remote)
        state, session = review.connection_context(request.user)
        if state and review.visible_commands(request.user).filter(state="conflict").exists():
            messages.warning(request, "Hay una operación por revisar. No se saltó ni se borró de la cola.")
        elif session and not session.online:
            messages.warning(request, "No llegó confirmación del servidor. Los pendientes siguen guardados.")
        else:
            messages.success(request, "Intento terminado. Consulta los estados; los demás pendientes continúan en segundo plano.")
    except (ProtocolError, ValueError, OSError, DatabaseError) as exc:
        action_error(request, exc)
    return redirect("local_sync")
