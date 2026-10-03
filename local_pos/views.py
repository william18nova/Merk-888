from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from mainApp.permissions import user_can_access_url_name
from .capabilities import MODULES
from django.conf import settings
from .models import ReplicaState, LocalCommand, LocalSaleSession


@login_required
def status(request):
    state = ReplicaState.objects.filter(node_id=settings.LOCAL_CONFIG["instance_id"]).first()
    owns_replica = not state or state.local_user_id == request.user.pk
    modules = [{"label": label, "route": route, "hint": "Consultar la copia autorizada →"} for label, route in MODULES
               if owns_replica and user_can_access_url_name(request.user, route) and (not state or route in state.scope.get("routes", []))]
    if owns_replica and state and getattr(settings, "HYBRID_LOCAL_SALES_ENABLED", False) and user_can_access_url_name(request.user, "generar_venta"):
        modules.insert(0, {"label": "Generar venta de prueba", "route": "generar_venta", "hint": "Abrir el carrito original →"})
    if owns_replica and state and getattr(settings, "HYBRID_LOCAL_OPERATIONS_ENABLED", False):
        from mainApp.permissions import user_can_change_sale
        if user_can_access_url_name(request.user, "registrar_egreso"):
            modules.insert(1, {"label": "Registrar pagos / egresos", "route": "registrar_egreso", "hint": "Registrar un pago de prueba →"})
        if user_can_change_sale(request.user):
            modules.insert(2, {"label": "Solicitar devoluciones", "route": "local_return", "hint": "Consultar venta y solicitar reintegro →"})
        if user_can_access_url_name(request.user, "turno_caja_cerrar"):
            modules.insert(3, {"label": "Declarar cierre de caja", "route": "turno_caja", "hint": "Pagos, efectivo y otros medios →"})
    session = LocalSaleSession.objects.filter(node_id=state.node_id).first() if owns_replica and state else None
    counts = None
    if owns_replica and state:
        commands = LocalCommand.objects.filter(node_id=state.node_id, actor=request.user)
        counts = {"pending": commands.filter(state__in=("intent", "pending")).count(),
                  "conflicts": commands.filter(state="conflict").count(),
                  "accepted": commands.filter(state="accepted").count()}
    return render(request, "local_pos/status.html", {"local_modules": modules, "replica_state": state if owns_replica else None,
                                                    "local_operation_counts": counts, "local_sale_session": session})
