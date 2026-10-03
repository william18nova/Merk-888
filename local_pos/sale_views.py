"""Adaptador Django de la página original; nunca usa settings de producción."""
import json
from pathlib import Path
from types import SimpleNamespace
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.http import JsonResponse, HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_GET

from hybrid_client.sale_page import read_endpoint
from pos_shared.protocol import ProtocolError
from mainApp.models import Inventario
from .models import LocalCommand, LocalSaleSession, ReplicaState
from . import sales
from .replica_transport import ReplicaRemote


class DemoRemote(ReplicaRemote):
    """Corte de transporte ficticio; únicamente el lanzador --sale-demo lo usa."""
    def call(self, action, data):
        if getattr(settings, "LOCAL_SALES_SIMULATE_OFFLINE", False):
            from hybrid_client.client import RemoteError
            raise RemoteError("Corte de prueba del origen ficticio.")
        return super().call(action, data)


def remote():
    path = Path(settings.LOCAL_CONFIG["data_dir"]) / "replica-connection.json"
    if path.is_symlink() or (path.stat().st_mode & 0o077 and __import__("os").name == "posix"):
        raise ProtocolError("La vinculación no está protegida.")
    cls = DemoRemote if getattr(settings, "LOCAL_SALES_DEMO_CONTROLS", False) else ReplicaRemote
    return cls(json.loads(path.read_text(encoding="utf-8")), allow_local=True)


def api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Inicia sesión en este equipo."}, status=401)
        try:
            state = sales.scope(request.user, new_sale=False)
            session = LocalSaleSession.objects.get(node_id=state.node_id)
            return view(request, state, session, *args, **kwargs)
        except (ProtocolError, ValueError, KeyError, LocalSaleSession.DoesNotExist, ReplicaState.DoesNotExist) as exc:
            return JsonResponse({"error": str(exc) if isinstance(exc, ProtocolError) else "Revisa la sesión y los datos de la solicitud."}, status=409)
    return wrapped


@login_required
@require_GET
def page(request):
    try:
        state = sales.scope(request.user)
        session = LocalSaleSession.objects.get(node_id=state.node_id)
    except (ProtocolError, LocalSaleSession.DoesNotExist, ReplicaState.DoesNotExist):
        return render(request, "local_pos/unavailable.html", {"local_message": "Primero prepara la autorización y el catálogo de ventas en este equipo."}, status=409)
    data = {**session.data, "django_runtime": True, "csrf_token": get_token(request)}
    return render(request, "generar_venta.html", {
        "hybrid_local": True, "hybrid_django_runtime": True, "hybrid_sale_context": data,
        "turno_requerido": True, "turno_activo": True, "venta_habilitada": True,
        "sucursal_nombre": data["branch"], "puntopago_nombre": data["point"], "turno_id": data["turn_id"],
        "form": {"initial": {"sucursal": data["branch_id"], "puntopago": data["point_id"]}},
        "payment_methods": data.get("payment_methods") or [{"code": "efectivo", "label": "Efectivo", "is_cash": True, "active": True}],
        "nequi_api_enabled": False,
    })


@api
def endpoint(request, state, session, action):
    if action == "test-network" and getattr(settings, "LOCAL_SALES_DEMO_CONTROLS", False):
        if request.method != "POST" or settings.LOCAL_CONFIG["database"] != "nova_full_local_lab":
            return JsonResponse({"error": "Solo en el laboratorio ficticio."}, status=405)
        body = json.loads(request.body)
        if type(body.get("offline")) is not bool:
            return JsonResponse({"error": "Estado inválido."}, status=400)
        settings.LOCAL_SALES_SIMULATE_OFFLINE = body["offline"]
        LocalSaleSession.objects.filter(pk=state.node_id).update(online=not body["offline"])
        if not body["offline"]:
            sales.flush(remote())
        return JsonResponse({"simulated_offline": body["offline"]})
    if action == "checkout":
        if request.method != "POST":
            return JsonResponse({"error": "Usa POST."}, status=405)
        if request.content_type != "application/json" or len(request.body) > 100000:
            return JsonResponse({"error": "Solicitud inválida."}, status=400)
        return JsonResponse(sales.checkout(request.user, json.loads(request.body), remote()))
    if request.method != "GET":
        return JsonResponse({"error": "Solo consulta."}, status=405)
    commands = LocalCommand.objects.filter(node_id=state.node_id, actor=request.user, kind__in=sales.KINDS,
                                           payload__session_id=str(session.session_id))
    if action == "status":
        pending = commands.exclude(state="accepted")
        closing = session.data.get("closing") or session.data.get("closed") or session.data.get("close_draft", {}).get("step", 1) > 1
        return JsonResponse({"unlocked": not state.blocked, "ready": bool(session.products) and not closing,
            "session": {**session.data, "expires_at": min(session.data["expires_at"], state.expires_at.isoformat())}, "online": session.online, "pending": pending.count(),
            "conflicts": commands.filter(state="conflict").count(), "error": session.error})
    if action == "history":
        return JsonResponse([sales.public_operation(row) for row in commands.filter(kind=sales.KIND).order_by("-sequence")[:50]], safe=False)
    if action.startswith("operation/"):
        row = commands.filter(pk=UUID(action.split("/", 1)[1])).first()
        return JsonResponse(sales.public_operation(row) if row else {"error": "Referencia no encontrada."}, status=200 if row else 404)
    return JsonResponse({"error": "Operación no disponible."}, status=404)


@api
def lookup(request, state, session, name):
    sales.scope(request.user)
    if request.method != "GET" and not (request.method == "POST" and name == "verificar_producto"):
        return JsonResponse({"error": "Solo consulta."}, status=405)
    stocks = dict(Inventario.objects.filter(sucursalid_id=state.scope["branch_id"]).values_list("productoid_id", "cantidad"))
    products = {key: {**row, "stock": stocks.get(row["id"], 0)} for key, row in session.products.items()}
    store = SimpleNamespace(products=lambda: products, get=lambda key: session.data if key == "session" else None)
    return JsonResponse(read_endpoint(SimpleNamespace(store=store), name, request.POST if request.method == "POST" else request.GET))


@login_required
@require_GET
def asset(request, name):
    sales.enabled()
    if name not in {"sale-bridge.js", "sale-bridge.css"}:
        return HttpResponse(status=404)
    data = (Path(settings.BASE_DIR) / "hybrid_client/assets" / name).read_bytes()
    return HttpResponse(data, content_type="text/javascript" if name.endswith(".js") else "text/css")
