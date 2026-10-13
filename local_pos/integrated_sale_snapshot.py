"""Bounded sales authority copied with the catalog, never from browser fields."""
def capture(identity, scope):
    from mainApp.models import Cliente, ClienteEspecial, Empleado, TurnoCaja
    from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE, is_feature_enabled
    from mainApp.services.printing import get_print_profile
    from mainApp.views import GenerarVentaView
    from mainApp.models import PuntosPago

    required = is_feature_enabled(TURN_REQUIRED_FEATURE, fresh=True)
    turn = TurnoCaja.objects.filter(cajero_id=scope["user_id"], estado__in=["ABIERTO", "CIERRE"]).order_by("-inicio").first()
    documents = {GenerarVentaView._normalize_document(value) for value in
        Empleado.objects.values_list("numerodocumento", flat=True)} - {""}
    special = set(ClienteEspecial.objects.filter(activo=True).values_list("cliente_id", flat=True))
    clients = [client.pk for client in Cliente.objects.all() if client.pk not in special
        and GenerarVentaView._normalize_document(client.numerodocumento) not in documents] if scope["clients"] else []
    profile = get_print_profile(PuntosPago.objects.get(pk=scope["point_id"]), fresh=True)
    scope["offline_sale"] = {
        "required_turn": required,
        "turn_id": turn.pk if required and turn and turn.puntopago_id == scope["point_id"] else None,
        "turn_started": turn.inicio.isoformat() if required and turn else None,
        "clients": clients,
        "print_operating_system": profile.sistema_operativo,
        "print_paper_size": profile.tamano_factura,
        "print_auto_cut": profile.corte_automatico,
    }
    from .integrated_discounts import capture as capture_discounts
    capture_discounts(scope)
    for routes in (scope["routes"], identity["routes"]):
        for route in ("generar_venta", "verificar_producto", "buscar_producto_por_codigo"):
            if route not in routes:
                routes.append(route)


def apply(scope):
    from django.utils.dateparse import parse_datetime
    from mainApp.models import ConfiguracionFuncionalidad, TurnoCaja
    from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE, NEQUI_API_FEATURE
    grant = scope.get("offline_sale")
    if not grant:
        return
    for key, enabled in ((TURN_REQUIRED_FEATURE, grant["required_turn"]), (NEQUI_API_FEATURE, False)):
        ConfiguracionFuncionalidad.objects.using("offline").update_or_create(clave=key,
            defaults={"habilitada": enabled, "actualizada_por_id": None})
    # These are private READ snapshots, not a cloud close operation. Keep history.
    TurnoCaja.objects.using("offline").filter(cajero_id=scope["user_id"]).exclude(
        pk=grant["turn_id"]).update(estado="CERRADO")
    if grant["turn_id"]:
        TurnoCaja.objects.using("offline").bulk_create([TurnoCaja(id=grant["turn_id"],
            cajero_id=scope["user_id"], puntopago_id=scope["point_id"], estado="ABIERTO")],
            update_conflicts=True, unique_fields=["id"], update_fields=["cajero", "puntopago", "estado"])
        TurnoCaja.objects.using("offline").filter(pk=grant["turn_id"]).update(inicio=parse_datetime(grant["turn_started"]))
