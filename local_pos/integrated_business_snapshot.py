"""Small, permission-scoped supporting snapshot for the original POS pages.

Not a database clone: only the operator's last 30 days / 2000 sales at the
bound point are eligible for offline refunds. No Nequi credentials/messages.
"""
import json
from datetime import timedelta
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone

ROUTES = ("registrar_egreso", "visualizar_ventas", "ventas_datatable", "ver_venta",
    "turno_caja", "turno_caja_iniciar", "turno_recuperar_o_iniciar", "turno_caja_puntopago_ac", "turno_caja_cajero_ac", "turno_caja_iniciar_cierre", "turno_caja_cerrar",
    "turno_caja_cierre_pagos", "turno_caja_cierre_efectivo", "turno_caja_cierre_medios")


def rows(query):
    return json.loads(json.dumps(list(query.values()), cls=DjangoJSONEncoder))


def capture(identity, scope):
    from mainApp.models import ConceptoEgreso, Venta, DetalleVenta, PagoVenta, TurnoCaja, OperacionPTM
    from mainApp.permissions import user_can_access_url_name, user_can_change_sale
    actor = identity["actor"]
    granted = [route for route in ROUTES if user_can_access_url_name(actor, route)]
    for routes in (scope["routes"], identity["routes"]):
        routes.extend(route for route in granted if route not in routes)
    data = {"version": 1, "concepts": [], "sales": [], "details": [], "payments": [], "turn": None}
    if "registrar_egreso" in granted:
        data["concepts"] = list(ConceptoEgreso.objects.order_by("nombre").values_list("nombre", flat=True)[:5000])
    if user_can_change_sale(actor):
        sales = Venta.objects.filter(empleadoid=identity["employee"], puntopagoid_id=scope["point_id"],
            fecha__gte=timezone.localdate() - timedelta(days=30)).order_by("-pk")
        if not scope["clients"]:
            sales = sales.filter(clienteid__isnull=True)
        data["sales"] = rows(sales[:2000])
        ids = [sale["ventaid"] for sale in data["sales"]]
        data["details"] = rows(DetalleVenta.objects.filter(ventaid_id__in=ids).order_by("pk")[:20001])
        data["payments"] = rows(PagoVenta.objects.filter(ventaid_id__in=ids).order_by("pk")[:10001])
        if len(data["details"]) > 20000 or len(data["payments"]) > 10000:
            from .replica import ReplicaError
            raise ReplicaError("El historial autorizado supera el límite local. Se conservó la copia anterior; requiere ajustar el alcance.")
    turn_id = scope["offline_sale"]["turn_id"]
    if turn_id:
        data["turn"] = rows(TurnoCaja.objects.filter(pk=turn_id))[0]
        data["has_ptm"] = OperacionPTM.objects.filter(turno_id=turn_id).exists()
    scope["offline_business"] = data


def _upsert(model, values):
    if not values:
        return
    fields = model._meta.concrete_fields
    objects = [model(**{field.attname: field.to_python(row[field.attname]) for field in fields}) for row in values]
    model.objects.using("offline").bulk_create(objects, update_conflicts=True,
        unique_fields=[model._meta.pk.name], update_fields=[field.name for field in fields if not field.primary_key])
    # auto_now_add ignores the supplied historical timestamp during INSERT.
    auto = [field for field in fields if getattr(field, "auto_now_add", False)]
    for row in values:
        if auto:
            model.objects.using("offline").filter(pk=row[model._meta.pk.attname]).update(
                **{field.name: field.to_python(row[field.attname]) for field in auto})


def apply(scope):
    from mainApp.models import Venta, DetalleVenta, PagoVenta, TurnoCaja
    data = scope.get("offline_business")
    if not data:
        return
    if data["turn"]:
        _upsert(TurnoCaja, [data["turn"]])
    _upsert(Venta, data["sales"])
    _upsert(DetalleVenta, data["details"])
    _upsert(PagoVenta, data["payments"])
