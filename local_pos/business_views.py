"""Formularios locales bajo el mismo menú, permisos y diseño del proyecto."""
from decimal import Decimal
from uuid import uuid4
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.http import HttpResponseNotAllowed
from django.utils import timezone
from pos_shared.protocol import ProtocolError, amount
from hybrid_client.client import RemoteError
from . import expenses, operations, sales
from .models import LocalSaleSession, LocalCommand
from .sale_views import remote

DENOMINATIONS = (100000, 50000, 20000, 10000, 5000, 2000, 1000, 500, 200, 100, 50)


def operation_context(request):
    if not getattr(settings, "HYBRID_LOCAL_OPERATIONS_ENABLED", False):
        raise ProtocolError("Este módulo local aún no está habilitado.")
    state = sales.scope(request.user, new_sale=False)
    session = LocalSaleSession.objects.get(node_id=state.node_id)
    return state, session


def unavailable(request, message):
    return render(request, "local_pos/unavailable.html", {"local_message": message}, status=409)


@login_required
def expense_page(request):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    try:
        state, session = operation_context(request)
        if not session.data.get("operation_permissions", {}).get("expense"):
            raise ProtocolError("No tienes autorización para registrar pagos.")
    except (ProtocolError, LocalSaleSession.DoesNotExist) as exc:
        return unavailable(request, str(exc))
    error = ""
    opid = request.POST.get("operation_id") or str(uuid4())
    if request.method == "POST":
        try:
            # La regla que vio el usuario debe coincidir con la autorización.
            taxes = request.POST.get("expected_tax")
            if taxes not in {"true", "false"}:
                raise ProtocolError("Revisa el impuesto antes de registrar.")
            data = {"operation_id": opid, "session_id": request.POST.get("session_id"),
                    "concept": request.POST.get("concept", ""), "amount_base": request.POST.get("amount_base"),
                    "method": request.POST.get("method"), "expected_tax": taxes == "true"}
            result = expenses.create(request.user, data, remote())
            request.session["local_last_operation"] = result
            return redirect("registrar_egreso")
        except (ProtocolError, ValueError, RemoteError) as exc:
            error = str(exc)
    history = [sales.public_operation(row) for row in LocalCommand.objects.filter(node_id=state.node_id,
        actor=request.user, kind=expenses.KIND).order_by("-sequence")[:50]]
    return render(request, "local_pos/business.html", {"mode": "expense", "title": "Registrar pago",
        "session": session, "methods": expenses.methods(session), "concepts": session.data.get("expense_concepts", []),
        "history": history, "operation_id": opid, "error": error, "last_operation": request.session.pop("local_last_operation", None)})


@login_required
def return_page(request):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    try:
        state, session = operation_context(request)
        if not session.data.get("operation_permissions", {}).get("return"):
            raise ProtocolError("No tienes permiso para devoluciones.")
    except (ProtocolError, LocalSaleSession.DoesNotExist) as exc:
        return unavailable(request, str(exc))
    error, snapshot = "", None
    sale_id = request.POST.get("sale_id") or request.GET.get("sale_id")
    opid = request.POST.get("operation_id") or str(uuid4())
    try:
        if sale_id:
            if request.method == "GET":
                snapshot = operations.sale_snapshot(request.user, int(sale_id), remote())
            else:
                snapshot = session.data.get("return_snapshots", {}).get(str(int(sale_id)))
                if not snapshot:
                    raise ProtocolError("Consulta primero la venta.")
                items = [{"detail_id": row["detail_id"], "quantity": int(request.POST.get("qty_"+str(row["detail_id"]), "0"))}
                         for row in snapshot["sale"]["items"]]
                items = [row for row in items if row["quantity"] > 0]
                total = operations.return_total(snapshot, items)
                result = operations.create_return(request.user, {"operation_id": opid,
                    "session_id": request.POST.get("session_id"), "data": {"sale_id": int(sale_id), "items": items,
                        "refunds": {request.POST.get("method"): str(total)}, "expected_total": request.POST.get("expected_total"),
                        "authorization": request.POST.get("authorization")}}, remote())
                request.session["local_last_operation"] = result
                return redirect("local_return")
    except (ProtocolError, ValueError, RemoteError) as exc:
        error = str(exc)
    history = [sales.public_operation(row) for row in LocalCommand.objects.filter(node_id=state.node_id,
        actor=request.user, kind="return.v1").order_by("-sequence")[:50]]
    return render(request, "local_pos/business.html", {"mode": "return", "title": "Devoluciones",
        "session": session, "methods": session.data.get("payment_methods", []), "snapshot": snapshot,
        "history": history, "operation_id": opid, "error": error, "last_operation": request.session.pop("local_last_operation", None)})


@login_required
def close_page(request):
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])
    try:
        state, session = operation_context(request)
        if not session.data.get("operation_permissions", {}).get("turn.close") or not session.data.get("turn_id"):
            raise ProtocolError("No tienes un turno autorizado para cerrar en este equipo.")
    except (ProtocolError, LocalSaleSession.DoesNotExist) as exc:
        return unavailable(request, str(exc))
    draft = session.data.get("close_draft", {"step": 1, "operation_id": str(uuid4())})
    error = ""
    methods = [row for row in session.data.get("payment_methods", []) if not row.get("is_cash")]
    if request.method == "POST" and not session.data.get("closing"):
        try:
            if request.POST.get("step") != str(draft["step"]):
                raise ProtocolError("Ese paso ya se confirmó. Continúa con el paso actual.")
            if draft["step"] == 1:
                fields = {"bills_paid": str(amount(request.POST.get("bills_paid", "0"))), "step": 2}
            elif draft["step"] == 2:
                count = {value: int(request.POST.get("denom_"+str(value), "0")) for value in DENOMINATIONS}
                if any(not 0 <= n <= 100000 for n in count.values()):
                    raise ProtocolError("Revisa las cantidades de billetes y monedas.")
                fields = {"cash_counted": str(amount(sum(value*n for value, n in count.items()))), "step": 3}
            else:
                result = operations.create_close(request.user, {"operation_id": draft["operation_id"],
                    "session_id": str(session.session_id), "data": {"turn_id": session.data["turn_id"],
                        "bills_paid": draft["bills_paid"], "cash_counted": draft["cash_counted"],
                        "methods": {row["code"]: request.POST.get("method_"+row["code"], "0") for row in methods},
                        "ptm_count": int(request.POST.get("ptm_count", "0"))}}, remote())
                request.session["local_last_operation"] = result
            if draft["step"] < 3:
                from django.db import transaction
                from .models import LocalNode
                with transaction.atomic():
                    LocalNode.objects.select_for_update().get(pk=state.node_id)
                    current = LocalSaleSession.objects.get(node_id=state.node_id)
                    saved = current.data.get("close_draft", {"step": 1, "operation_id": draft["operation_id"]})
                    if saved["step"] != draft["step"] or current.data.get("closing"):
                        raise ProtocolError("Otra pestaña ya confirmó este paso. Recarga para continuar.")
                    sales.scope(request.user)
                    current.data = {**current.data, "close_draft": {**saved, **fields}}
                    current.save(update_fields=["data"])
            return redirect("turno_caja")
        except (ProtocolError, ValueError, RemoteError) as exc:
            error = str(exc)
    history = [sales.public_operation(row) for row in LocalCommand.objects.filter(node_id=state.node_id,
        actor=request.user, kind="turn.close.v1").order_by("-sequence")[:10]]
    return render(request, "local_pos/business.html", {"mode": "close", "title": "Cierre de caja",
        "session": session, "methods": methods, "draft": draft, "denominations": DENOMINATIONS,
        "history": history, "error": error, "last_operation": request.session.pop("local_last_operation", None)})
