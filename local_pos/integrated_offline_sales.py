"""Original POS offline adapter: durable domain events, not form/SQL replay.

One local commit stores the ticket, outbox and stock projection. Cloud commit
stores the business sale and UUID receipt together. A missing ACK retries that
same UUID. No worker ever marks a failed network request as a rejection.
"""
import json
from decimal import Decimal, InvalidOperation
from uuid import UUID

from django.conf import settings
from django.db import connections, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.utils.crypto import salted_hmac, constant_time_compare
from django.utils.dateparse import parse_datetime

from mainApp.models import (Usuario, Producto, Inventario, Cliente, PuntosPago,
    TurnoCaja, SolicitudPOS)
from mainApp.permissions import user_can_access_url_name, _load_permission_state
from mainApp.services.pos_request_receipts import HEADER, failure, request_fingerprint, lock_operation
from pos_shared.replica import canonical, manifest
from .integrated_auth import authorized_identity, cloud_fingerprint
from .models import LocalCommand, LocalNode, ReplicaState, ReplicaRow
from .request_context import offline_request

KIND = "integrated.sale.offline.v1"


class SaleError(ValueError):
    pass


def seal(payload):
    return salted_hmac("nova.integrated.offline-event.v1", canonical(payload), algorithm="sha256").hexdigest()


def original_response(command):
    # The local ticket stays stable even if an ACK reached the worker first.
    response = JsonResponse(command.local_result["response"])
    response[HEADER] = str(command.pk)
    response["Cache-Control"] = "no-store"
    return response


def replay(request, command):
    if (command.node_id != UUID(str(settings.LOCAL_CONFIG["instance_id"]))
            or command.actor_id != request.user.pk or command.fingerprint != request_fingerprint(request)
            or command.payload.get("source") != cloud_fingerprint()):
        raise SaleError("Esta referencia no coincide con el carrito guardado. No repitas el cobro.")
    return original_response(command)


def build_event(request, state, operation_id):
    from mainApp.forms import GenerarVentaForm
    from mainApp.views import GenerarVentaView, _round_account_peso
    from mainApp.services.payment_methods import active_payment_method_codes

    grant = state.scope.get("offline_sale")
    if not grant or (grant["required_turn"] and not grant["turn_id"]):
        raise SaleError("Primero actualiza la copia con un turno abierto en esta caja.")
    if grant["turn_id"] and not TurnoCaja.objects.filter(pk=grant["turn_id"], estado="ABIERTO").exists():
        raise SaleError("El cierre ya comenzó. No puedes agregar ventas a ese turno.")
    form = GenerarVentaForm(request.POST)
    if not form.is_valid():
        raise SaleError("Revisa los datos de la venta; el formulario no es válido.")
    data = form.cleaned_data
    if (data["sucursal"].pk != state.scope["branch_id"] or data["puntopago"].pk != state.scope["point_id"]):
        raise SaleError("Esta venta no pertenece a la sucursal y caja preparadas en este equipo.")
    if data.get("nequi_notificacion_id"):
        raise SaleError("Sin conexión no se puede verificar una notificación Nequi. Consérvala para vincularla con internet.")
    client_id = data.get("cliente_id")
    if client_id and (client_id not in grant["clients"] or not Cliente.objects.filter(pk=client_id).exists()):
        raise SaleError("Este cliente requiere validación con internet antes de facturar.")
    ids, quantities = data["productos"], data["cantidades"]
    if not ids or len(ids) > 500 or len(ids) != len(quantities):
        raise SaleError("El carrito está vacío o sus cantidades no coinciden.")
    items = []
    for product_id, quantity in zip(ids, quantities):
        if isinstance(product_id, bool) or isinstance(quantity, bool):
            raise SaleError("Producto o cantidad inválidos.")
        if not str(product_id).isdigit() or not str(quantity).isdigit() or not 1 <= int(quantity) <= 1000000:
            raise SaleError("Las ventas offline admiten cantidades positivas en unidades o gramos. Las devoluciones requieren conexión.")
        product = Producto.objects.filter(pk=int(product_id)).first()
        if not product or product.tipo_ptm or not Inventario.objects.filter(
                productoid=product, sucursalid_id=state.scope["branch_id"]).exists():
            raise SaleError("Un producto no está disponible en esta copia, o pertenece a PTM.")
        price = Decimal(product.precio)
        if not price.is_finite() or price < 0:
            raise SaleError("El precio del producto no es válido.")
        items.append({"productoid": product.pk, "producto": product.nombre, "cantidad": int(quantity),
            "precio_unitario": price, "subtotal": price * int(quantity)})
    items, total = GenerarVentaView._apply_bag_promo(items)
    total = _round_account_peso(total)
    if total <= 0 or total >= Decimal("1000000000000"):
        raise SaleError("El total de esta venta necesita revisión con conexión.")
    subtotal = total
    from .integrated_discounts import sale_discount
    discount, total = sale_discount(request, state, data, subtotal)
    try:
        expected = Decimal(request.headers.get("X-Nova-Expected-Total", ""))
    except InvalidOperation:
        raise SaleError("Actualiza el carrito: falta confirmar el total mostrado.") from None
    if not expected.is_finite() or expected != total:
        raise SaleError("El total del carrito no coincide con los precios locales. Revisa los productos antes de cobrar.")
    payments = GenerarVentaView._normalize_payments(data["pagos"], total, data["medio_pago"],
        allowed_codes=active_payment_method_codes())
    if total > 0 and not payments:
        raise SaleError("Los pagos deben cubrir exactamente el total con medios activos.")
    if total == 0:
        payments = []
    if any(p["medio_pago"] == "nequi" for p in payments):
        raise SaleError("Nequi no está habilitado para nuevas ventas sin conexión.")
    received = data["efectivo_recibido"]
    cash = sum((p["monto"] for p in payments if p["medio_pago"] == "efectivo"), Decimal(0))
    if not received.is_finite() or received < 0 or (received and received < cash):
        raise SaleError("El efectivo recibido no cubre la parte pagada en efectivo.")
    occurred = timezone.now()
    payload = {"source": cloud_fingerprint(), "operation_id": str(operation_id),
        "actor_id": request.user.pk, "node_id": str(state.node_id), "snapshot_id": str(state.active_id),
        "authorized_until": state.expires_at.isoformat(), "occurred_at": occurred.isoformat(),
        "branch_id": state.scope["branch_id"], "point_id": state.scope["point_id"],
        "turn_id": grant["turn_id"], "turn_operation": grant.get("turn_operation"),
        "required_turn": grant["required_turn"], "client_id": client_id,
        "discount": discount, "subtotal": str(subtotal),
        "items": [{key: str(value) if isinstance(value, Decimal) else value for key, value in item.items()} for item in items],
        "payments": [{"medio_pago": p["medio_pago"], "monto": str(p["monto"])} for p in payments],
        "total": str(total), "cash_received": str(received)}
    reference = "LOCAL-" + str(operation_id)
    employee = request.user.empleado
    text = GenerarVentaView._build_receipt_text({"venta_id": reference, "fecha_hora": timezone.localtime(occurred),
        "sucursal_nombre": data["sucursal"].nombre, "cajero_nombre": str(employee),
        "efectivo_recibido": received or cash, "cambio": max(Decimal(0), received - cash) if cash else 0,
        "descuento_empleado": subtotal - total},
        items, total, payments, paper_size=grant["print_paper_size"])
    text = ("COMPROBANTE LOCAL\nPENDIENTE DE ENVIO\nReferencia completa:\n"
        + str(operation_id)[:18] + "\n" + str(operation_id)[18:] + "\n"
        + text.replace("FACTURA\n", "VENTA LOCAL\n").replace("Factura #", "Referencia #"))
    response = {"success": True, "venta_id": reference, "operation_id": str(operation_id), "offline_saved": True,
        "sale_total": str(total), "receipt_text": text, "nequi_linked": False,
        **{key: grant[key] for key in ("print_operating_system", "print_paper_size", "print_auto_cut")}}
    return payload, response


def save(request):
    from .integrated_journal import operation_lock
    try:
        operation_id = UUID(request.headers.get(HEADER, ""))
        if operation_id.version != 4:
            raise ValueError
    except (ValueError, TypeError):
        return failure("Falta una referencia válida para proteger la venta.", "operation_required", 409)
    try:
        with operation_lock(operation_id) as locked:
            if not locked:
                return failure("Esta venta se está guardando. Espera unos segundos.", "operation_busy", 409)
            from .integrated_discounts import verify_sale_password
            verify_sale_password(request, operation_id)
            with transaction.atomic(using="offline"):
                node = LocalNode.objects.using("offline").select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
                previous = LocalCommand.objects.using("offline").filter(pk=operation_id).first()
                if previous:
                    if previous.kind == KIND:
                        return replay(request, previous)
                    from .integrated_journal import KIND as ONLINE_KIND, TERMINAL_RETRY
                    # Only a verified absence or rolled-back validation permits
                    # this transition. A timeout is NEVER proof of absence.
                    if (previous.kind != ONLINE_KIND or previous.state != "accepted"
                            or previous.local_result.get("outcome") not in TERMINAL_RETRY
                            or previous.actor_id != request.user.pk or previous.node_id != node.pk
                            or previous.payload != {"source": cloud_fingerprint()}):
                        raise SaleError("Esta referencia ya se intentó en la nube. Espera su confirmación; no repitas el cobro.")
                state = ReplicaState.objects.using("offline").get(node=node)
                if (not offline_request() or not authorized_identity(request.user) or not request.user.is_active
                        or not user_can_access_url_name(request.user, "generar_venta")
                        or state.local_user_id != request.user.pk or state.blocked or not state.active_id
                        or state.device_id != node.pk or state.server != "django-db:" + cloud_fingerprint()
                        or not state.expires_at or state.expires_at <= timezone.now()):
                    raise SaleError("La copia offline no tiene autorización vigente para este usuario.")
                now = timezone.now()
                last = LocalCommand.objects.using("offline").filter(node=node, kind=KIND).order_by("-sequence").first()
                if state.synchronized_at > now or (last and last.created_at > now):
                    raise SaleError("La hora del equipo retrocedió. Corrígela antes de registrar otra venta.")
                payload, response = build_event(request, state, operation_id)
                node.sequence += 1
                node.save(using="offline", update_fields=["sequence"])
                result = {"outcome": "saved_offline", "seal": seal(payload), "response": response}
                if previous:
                    result["previous_online_outcome"] = previous.local_result["outcome"]
                command, _ = LocalCommand.objects.using("offline").update_or_create(operation_id=operation_id,
                    defaults={"node": node, "actor_id": request.user.pk, "sequence": node.sequence,
                        "kind": KIND, "state": "pending", "fingerprint": request_fingerprint(request),
                        "payload": payload, "local_result": result})
                for item in payload["items"]:
                    stock = Inventario.objects.using("offline").select_for_update().get(
                        productoid_id=item["productoid"], sucursalid_id=payload["branch_id"])
                    stock.cantidad -= item["cantidad"]  # Negative inventory is intentional.
                    stock.save(using="offline", update_fields=["cantidad"])
                    row = ReplicaRow.objects.using("offline").get(node=node, entity="stock", source_id=str(stock.pk))
                    row.values = {**row.values, "cantidad": stock.cantidad}
                    row.save(using="offline", update_fields=["values"])
                rows = [{"entity": r.entity, "id": r.source_id, "values": r.values}
                    for r in ReplicaRow.objects.using("offline").filter(node=node)]
                state.manifest = manifest(rows)
                state.scope = {**state.scope, "refresh_required": True}
                state.save(using="offline", update_fields=["scope", "manifest"])
            return original_response(command)  # ONLY after local commit.
    except SaleError as exc:
        return failure(str(exc), "offline_sale_validation", 409)


def upload(command):
    """Bounded independent cloud connection, idempotent atomic business commit."""
    from mainApp.views import GenerarVentaView
    from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE, locked_feature_enabled
    from mainApp.services.special_discount import is_special_client
    payload = command.payload
    if (offline_request() or payload.get("source") != cloud_fingerprint()
            or payload.get("operation_id") != str(command.pk) or payload.get("actor_id") != command.actor_id
            or payload.get("node_id") != str(command.node_id)
            or not constant_time_compare(command.local_result.get("seal", ""), seal(payload))):
        raise SaleError("El evento guardado no coincide con su autorización.")
    occurred = parse_datetime(payload["occurred_at"])
    expires = parse_datetime(payload["authorized_until"])
    if not occurred or not expires or occurred > expires or occurred > timezone.now():
        raise SaleError("La fecha de la venta no coincide con su autorización.")
    cloud = connections["default"]
    if cloud.in_atomic_block:
        raise RuntimeError("La sincronización necesita una conexión independiente.")
    original = cloud.settings_dict
    cloud.close()
    cloud.settings_dict = {**original, "OPTIONS": {**original.get("OPTIONS", {}),
        "connect_timeout": 6, "options": "-c statement_timeout=15000 -c lock_timeout=3000"}}
    try:
        with transaction.atomic():
            lock_operation(command.pk)
            receipt = SolicitudPOS.objects.filter(pk=command.pk).first()
            if receipt:
                if receipt.usuario_id != command.actor_id or receipt.huella != command.fingerprint or receipt.accion != "sale":
                    raise SaleError("La referencia ya pertenece a otra operación.")
                if not receipt.respuesta:
                    raise SaleError("La confirmación existente necesita revisión.")
                return receipt.respuesta
            actor = Usuario.objects.select_related("rolid", "empleado").filter(pk=command.actor_id).first()
            if actor:
                _load_permission_state(actor, fresh=True)
            if (not actor or not actor.is_active or not user_can_access_url_name(actor, "generar_venta")
                    or not getattr(actor, "empleado", None) or actor.empleado.sucursalid_id != payload["branch_id"]):
                raise SaleError("El usuario perdió su autorización o cambió de sucursal.")
            point = PuntosPago.objects.filter(pk=payload["point_id"], sucursalid_id=payload["branch_id"]).first()
            if not point:
                raise SaleError("La caja cambió de sucursal o ya no existe.")
            required = locked_feature_enabled(TURN_REQUIRED_FEATURE)
            from .integrated_turns import resolve_turn
            turn = TurnoCaja.objects.select_for_update().filter(pk=resolve_turn(payload), cajero=actor,
                puntopago=point, estado="ABIERTO").first() if required else None
            if required != payload["required_turn"] or (required and (not turn or occurred < turn.inicio)):
                raise SaleError("El turno cambió o fue cerrado; esta venta necesita revisión.")
            client = Cliente.objects.filter(pk=payload["client_id"]).first() if payload["client_id"] else None
            if payload["client_id"] and not client:
                raise SaleError("La autorización de este cliente cambió; se necesita revisión.")
            from .integrated_discounts import validate_upload
            from mainApp.models import AutorizacionDescuentoEspecial
            try:
                discount_args = validate_upload(payload, client, actor, occurred)
            except (ValueError, AutorizacionDescuentoEspecial.DoesNotExist) as exc:
                raise SaleError("La autorización del descuento requiere revisión: " + str(exc)) from None
            ids = {item["productoid"] for item in payload["items"]}
            if set(Producto.objects.filter(pk__in=ids, tipo_ptm__isnull=True).values_list("pk", flat=True)) != ids:
                raise SaleError("Un producto cambió o ya no existe; se necesita revisión.")
            items = [{**item, "precio_unitario": Decimal(item["precio_unitario"]), "subtotal": Decimal(item["subtotal"])}
                for item in payload["items"]]
            payments = [{**p, "monto": Decimal(p["monto"])} for p in payload["payments"]]
            result = GenerarVentaView._crear_venta_ultra_fast(actor, point.sucursalid, point, payload["client_id"],
                payments, items, Decimal(payload["total"]), Decimal(payload["cash_received"]),
                cliente_inst=client, turno=turn, turno_requerido=required, occurred_at=occurred, propagate_errors=True,
                **discount_args)
            data = json.loads(result.content)
            if data.get("success") is not True or type(data.get("venta_id")) is not int:
                raise SaleError("La nube no aceptó los datos de la venta; revisa turno y medios de pago.")
            data.pop("print_token", None)
            if payload.get("discount"):
                proof = payload["discount"]
                data["offline_discount"] = {"kind": proof["kind"], "amount": proof["amount"],
                    "authorized_by": proof.get("user_id") or proof.get("authorized_by"),
                    "special_authorization_id": proof.get("id"), "at": payload["occurred_at"],
                    "node_id": payload["node_id"]}
            SolicitudPOS.objects.create(pk=command.pk, usuario=actor, accion="sale", huella=command.fingerprint,
                venta_id=data["venta_id"], respuesta=data)
            return data
    finally:
        cloud.close()
        cloud.settings_dict = original


def flush(limit=20):
    from .integrated_journal import operation_lock
    from .integrated_business import KINDS, ONLINE_KIND, upload as upload_business, reconcile_online
    node_id = settings.LOCAL_CONFIG["instance_id"]
    count = 0
    for operation_id in LocalCommand.objects.using("offline").filter(node_id=node_id,
            state__in=["pending", "intent", "conflict"]).order_by("sequence").values_list("pk", flat=True)[:limit]:
        with operation_lock(operation_id) as locked:
            if not locked:
                break
            command = LocalCommand.objects.using("offline").get(pk=operation_id)
            if command.state == "conflict":
                break
            if command.state == "accepted":
                continue
            try:
                if command.kind == ONLINE_KIND:
                    reconcile_online(command)
                    count += 1
                    continue
                if command.kind == KIND:
                    response = upload(command)
                elif command.kind in KINDS.values():
                    response = upload_business(command)
                else:
                    break  # An earlier ambiguous original sale must resolve first.
            except SaleError as exc:
                LocalCommand.objects.using("offline").filter(pk=command.pk).update(state="conflict",
                    local_result={**command.local_result, "outcome": "review", "reason": str(exc)})
                break
            # A database/network/ACK failure escapes and leaves the SAME UUID pending.
            LocalCommand.objects.using("offline").filter(pk=command.pk).update(state="accepted",
                local_result={**command.local_result, "outcome": "synchronized", "cloud_response": response})
            count += 1
    return count
