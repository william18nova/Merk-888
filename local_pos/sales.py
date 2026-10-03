"""Ventas locales durables. Intención antes de red; proyección y diario atómicos."""
import copy
from datetime import timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from hybrid_client.client import RemoteError
from mainApp.models import Inventario, MetodoPago, Producto
from mainApp.permissions import user_can_access_url_name
from pos_shared.protocol import PROTOCOL, ProtocolError, amount, fingerprint, price_cart
from pos_shared.payments import split_payments
from .models import LocalNode, LocalCommand, LocalSaleSession, ReplicaState

KIND = "sale.cash.v1"
KINDS = {KIND, "expense.create.v1", "return.v1", "turn.close.v1"}
TRANSIENT = {408, 429, 500, 502, 503, 504}


def enabled():
    if not (getattr(settings, "HYBRID_LOCAL_ENABLED", False)
            and getattr(settings, "HYBRID_LOCAL_SALES_ENABLED", False)):
        raise ProtocolError("Las ventas no están habilitadas en este laboratorio.")


def scope(actor, *, using="default", new_sale=True):
    enabled()
    if not actor.is_authenticated or not actor.is_active or not user_can_access_url_name(actor, "generar_venta"):
        raise ProtocolError("No tienes permiso para vender en este equipo.")
    state = ReplicaState.objects.using(using).filter(node_id=settings.LOCAL_CONFIG["instance_id"]).first()
    if state is None:
        raise ProtocolError("Primero vincula el equipo y descarga su copia autorizada.")
    if state.local_user_id != actor.pk or not state.active_id:
        raise ProtocolError("La copia local no pertenece a esta sesión.")
    if new_sale and (state.blocked or not state.expires_at or state.expires_at <= timezone.now()):
        raise ProtocolError("La autorización local venció o fue retirada. Conserva los pendientes.")
    return state


def refresh_authorization(remote, *, local_user_id, using="default"):
    enabled()
    products = {}
    for _ in range(60):
        result = remote.call("catalog", {"known": {key: row["digest"] for key, row in products.items()}})
        if not isinstance(result.get("updates"), list) or not isinstance(result.get("deleted"), list):
            raise ProtocolError("Catálogo autorizado incompleto.")
        for row in result["updates"]:
            if (type(row.get("id")) is not int or not isinstance(row.get("quote"), str)
                    or not isinstance(row.get("digest"), str) or len(row["quote"]) > 8000):
                raise ProtocolError("Precio autorizado inválido.")
            amount(row["price"])
            products[str(row["id"])] = row
        for key in result["deleted"]:
            products.pop(str(key), None)
        if len(products) > 25000:
            raise ProtocolError("El catálogo supera el límite del piloto.")
        if result.get("more") is False:
            break
    else:
        raise ProtocolError("El catálogo autorizado no terminó de descargarse.")
    data = result.get("session", {})
    with transaction.atomic(using=using):
        node = LocalNode.objects.using(using).select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        state = ReplicaState.objects.using(using).get(node=node)
        if (state.local_user_id != local_user_id or str(state.device_id) != remote.device_id
                or state.server != remote.url or data.get("session_id") != remote.session_id
                or any(data.get(key) != state.scope.get(key) for key in ("user_id", "branch_id", "point_id"))):
            raise ProtocolError("La autorización de ventas corresponde a otro equipo o usuario.")
        expiry = parse_datetime(str(data.get("expires_at", "")))
        if not expiry or timezone.is_naive(expiry) or expiry <= timezone.now() or type(data.get("sequence")) is not int:
            raise ProtocolError("La autorización de ventas venció.")
        session = LocalSaleSession.objects.using(using).filter(node=node).first()
        if session and str(session.session_id) != remote.session_id:
            raise ProtocolError("Finaliza y concilia la sesión anterior antes de cambiarla.")
        if session is None:
            session = LocalSaleSession(node=node, session_id=remote.session_id, sequence=data["sequence"])
        if data["sequence"] > session.sequence:
            raise ProtocolError("Hay ventas remotas sin diario local; se requiere conciliación.")
        previous = session.data or {}
        data.update({key: previous[key] for key in ("closing", "closed", "return_snapshots", "close_draft") if key in previous})
        session.data, session.products, session.online = data, products, True
        session.error = ""
        session.save(using=using)
        from .sessions import synchronize_operator_permissions
        synchronize_operator_permissions(session, state, using=using)
    return session


def cursor_sequence(cursor, session_id):
    if not isinstance(cursor, dict) or cursor.get("session_id") != str(session_id):
        return 0
    sequence = cursor.get("sequence")
    if type(sequence) is not int or sequence < 0:
        raise ProtocolError("Confirmación de inventario inválida.")
    return sequence


def projected_rows(rows, cursor, *, node_id, using="default"):
    """Stock del corte remoto menos movimientos locales aún no incluidos en él."""
    projected = copy.deepcopy(rows)
    stocks = {row["values"]["productoid_id"]: row for row in projected if row["entity"] == "stock"}
    for command in LocalCommand.objects.using(using).filter(node_id=node_id, kind__in=KINDS):
        if not command.local_result.get("projected"):
            continue
        payload = command.payload
        if cursor.get("session_id") and payload["session_id"] != cursor["session_id"] and command.state == "accepted":
            continue  # Un corte de una sesión posterior ya incluye sesiones cerradas.
        if payload["sequence"] <= cursor_sequence(cursor, payload["session_id"]):
            continue
        for pid, delta in inventory_delta(command).items():
            if pid not in stocks:
                raise ProtocolError("Falta inventario de una venta local pendiente; se requiere conciliación.")
            stocks[pid]["values"]["cantidad"] += delta
    return projected


def inventory_delta(command):
    if command.kind == KIND:
        return {item["id"]: -item["quantity"] for item in command.payload["items"]}
    return {int(key): value for key, value in command.local_result.get("inventory_delta", {}).items()}


def project(command, state, using):
    if command.local_result.get("projected"):
        return
    payload = command.payload
    if payload["sequence"] > cursor_sequence(state.sale_cursor, payload["session_id"]):
        for pid, delta in inventory_delta(command).items():
            stock = Inventario.objects.using(using).select_for_update().get(
                sucursalid_id=state.scope["branch_id"], productoid_id=pid)
            stock.cantidad += delta  # Cero y negativos son válidos.
            stock.save(using=using, update_fields=["cantidad"])
    command.local_result = {**command.local_result, "projected": True}


def public_operation(command):
    result = command.local_result
    return {**{key: result[key] for key in ("expense_id", "turn_id", "summary", "total", "base", "tax", "concept", "method", "occurred_at", "message", "returned_total", "closed") if key in result},
            "id": str(command.pk), "kind": command.kind, "state": command.state, "sale_id": result.get("sale_id"),
            "receipt": result.get("receipt", ""), "error": result.get("error", ""),
            "printing": {"state": "not_requested"}}


def valid_ack(command, result):
    try:
        if not isinstance(result, dict) or result.get("status") != "accepted" or result.get("operation_id") != str(command.pk):
            return False
        local = command.local_result
        if command.kind == KIND:
            return (type(result.get("sale_id")) is int and result["sale_id"] > 0
                    and amount(result.get("total")) == amount(local["total"])
                    and amount(result.get("change")) == amount(local["change"]))
        if command.kind == "expense.create.v1":
            return (type(result.get("expense_id")) is int and result["expense_id"] > 0
                    and all(result.get(key) == local[key] for key in ("total", "base", "tax"))
                    and all(result.get(key) == local[key] for key in ("concept", "method")))
        if command.kind == "return.v1":
            return (result.get("kind") == command.kind and result.get("sale_id") == command.payload["data"]["sale_id"]
                    and amount(result.get("returned_total")) == amount(command.payload["data"]["expected_total"])
                    and result.get("inventory_delta") == local["expected_delta"])
        if command.kind == "turn.close.v1":
            return result.get("closed") is True and result.get("turn_id") == command.payload["data"]["turn_id"]
    except (ValueError, TypeError, KeyError):
        return False
    return False


def flush(remote, *, using="default", limit=100):
    enabled()
    from .sync_review import record_attempt
    # El bloqueo PostgreSQL serializa también otros procesos/pestañas, no solo
    # hilos de Python. La red tiene timeout corto y nunca se mantiene una
    # transacción del negocio de producción abierta desde el cliente.
    for _ in range(limit):
        with transaction.atomic(using=using):
            node = LocalNode.objects.using(using).select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
            session = LocalSaleSession.objects.using(using).get(node=node)
            state = ReplicaState.objects.using(using).get(node=node)
            if str(session.session_id) != remote.session_id or state.server != remote.url or str(state.device_id) != remote.device_id:
                raise ProtocolError("La cola pertenece a otra vinculación.")
            command = LocalCommand.objects.using(using).filter(node=node).exclude(state="accepted").order_by("sequence").first()
            if not command or command.state == "conflict":
                return
            if command.kind not in KINDS:
                raise ProtocolError("La cola contiene una operación desconocida. No se saltó su secuencia.")
            try:
                action = "sale" if command.kind == KIND else "expense" if command.kind == "expense.create.v1" else "operation"
                result = remote.call(action, command.payload)
                if not valid_ack(command, result):
                    raise RemoteError("Acuse inválido; se conserva la misma referencia.", 502, "ack_invalid")
            except RemoteError as exc:
                session.online = False
                if exc.status in TRANSIENT:
                    if command.kind == KIND:
                        project(command, state, using)
                    command.state = "pending"
                    session.error = "Sin confirmación de la nube; pendientes guardados."
                else:
                    command.state = "conflict"
                    command.local_result = {**command.local_result, "error": "La nube rechazó esta operación. Solicita revisión; no repitas el cobro."}
                    session.error = "Hay una operación que requiere revisión."
                    if exc.status in (401, 403):
                        state.blocked = True
                        state.save(using=using, update_fields=["blocked"])
                record_attempt(command, exc)
                command.save(using=using, update_fields=["state", "local_result"])
                session.save(using=using, update_fields=["online", "error"])
                return
            if command.kind == "return.v1":
                command.local_result = {**command.local_result, "inventory_delta": result["inventory_delta"], "projected": False}
            project(command, state, using)
            command.state = "accepted"
            command.local_result = {**command.local_result, **{key: result[key] for key in (
                "sale_id", "expense_id", "turn_id", "closed", "returned_total", "message") if key in result},
                "receipt": result.get("receipt_text") or command.local_result.get("receipt", ""), "error": ""}
            record_attempt(command)
            command.save(using=using, update_fields=["state", "local_result"])
            session.online, session.error = True, ""
            if command.kind == "turn.close.v1":
                session.data = {**session.data, "closed": True, "closing": True}
            session.save(using=using, update_fields=["online", "error", "data"])


def checkout(actor, data, remote, *, using="default"):
    state = scope(actor, using=using)
    if not isinstance(data, dict) or set(data) - {"operation_id", "session_id", "items", "cash_received", "expected_total", "payments"}:
        raise ProtocolError("Datos de venta no admitidos.")
    operation_id = UUID(str(data.get("operation_id")))
    identity = fingerprint({"actor": actor.pk, "request": data})
    with transaction.atomic(using=using):
        node = LocalNode.objects.using(using).select_for_update().get(pk=state.node_id)
        state = scope(actor, using=using)
        session = LocalSaleSession.objects.using(using).get(node=node)
        if str(session.session_id) != data.get("session_id"):
            raise ProtocolError("La pestaña pertenece a otra sesión.")
        old = LocalCommand.objects.using(using).filter(pk=operation_id).first()
        if old:
            if old.node_id != node.pk or old.fingerprint != identity:
                raise ProtocolError("Esta referencia pertenece a otro cobro; no se duplicó.")
        else:
            if session.data.get("closing") or session.data.get("closed") or session.data.get("close_draft", {}).get("step", 1) > 1:
                raise ProtocolError("Ya declaraste el cierre; no puedes registrar más ventas en este turno.")
            if LocalCommand.objects.using(using).filter(node=node, state="conflict").exists():
                raise ProtocolError("Hay una operación por revisar; no repitas el cobro.")
            now = timezone.now()
            if now >= parse_datetime(session.data["expires_at"]) or (session.last_clock and now < session.last_clock - timedelta(minutes=2)):
                raise ProtocolError("La autorización venció o el reloj retrocedió.")
            details, total = price_cart(data.get("items"), session.products)
            methods = session.data.get("payment_methods") or [{"code": "efectivo", "is_cash": True}]
            values = data.get("payments", [{"medio_pago": "efectivo", "monto": str(total)}])
            payments, received, change = split_payments(values, total, data.get("cash_received"), methods)
            codes = [row["medio_pago"] for row in payments]
            if MetodoPago.objects.using(using).filter(pk__in=codes, activo=True).count() != len(codes):
                raise ProtocolError("Un medio de pago ya no está activo en la copia local.")
            if data.get("expected_total") is not None and amount(data["expected_total"]) != total:
                raise ProtocolError("El efectivo o total no coincide. Revisa antes de cobrar.")
            ids = [item["id"] for item in data["items"]]
            if (Producto.objects.using(using).filter(pk__in=ids, tipo_ptm__isnull=True).count() != len(ids)
                    or Inventario.objects.using(using).filter(productoid_id__in=ids, sucursalid_id=state.scope["branch_id"]).count() != len(ids)):
                raise ProtocolError("Falta respaldo de un producto o inventario; actualiza antes de venderlo.")
            session.sequence += 1
            session.last_clock = max(now, session.last_clock or now)
            session.save(using=using, update_fields=["sequence", "last_clock"])
            node.sequence += 1
            node.save(using=using, update_fields=["sequence"])
            payload = {"protocol": PROTOCOL, "operation_id": str(operation_id), "session_id": str(session.session_id),
                       "sequence": session.sequence, "occurred_at": now.isoformat(), "cash_received": str(received),
                       "items": [{**item, "quote": session.products[str(item["id"])]["quote"]} for item in data["items"]]}
            if "payments" in data:
                by_code = {row["code"]: row for row in methods}
                payload["payments"] = [{"medio_pago": row["medio_pago"], "monto": str(row["monto"]),
                    "quote": by_code[row["medio_pago"]].get("quote", "")} for row in payments]
            lines = ["NOVA POS · COMPROBANTE LOCAL", "Referencia: " + str(operation_id), session.data["branch"],
                     session.data["point"], "Cajero: " + session.data["user"], now.isoformat(), ""]
            for item in details:
                lines.extend([item["producto"], f"{item['cantidad']} x {item['precio_unitario']} = {item['subtotal']}"])
            lines.extend([f"TOTAL: $ {total}", *[f"{row['medio_pago'].upper()}: $ {row['monto']}" for row in payments],
                          f"RECIBIDO EFECTIVO: $ {received}", f"CAMBIO: $ {change}"])
            if any(row["medio_pago"] != "efectivo" for row in payments):
                lines.append("Medios electrónicos declarados por el cajero; sin verificación bancaria automática.")
            LocalCommand.objects.using(using).create(operation_id=operation_id, node=node, sequence=node.sequence,
                actor_id=actor.pk, kind=KIND, payload=payload, fingerprint=identity, state="intent",
                local_result={"projected": False, "total": str(total), "change": str(change),
                              "receipt": "\n".join(lines)})
    # El commit de intención ya terminó antes de intentar el primer envío.
    # No retener al cajero drenando toda una cola grande en el POST. El proceso
    # de fondo continúa la secuencia; el cobro actual queda durable igualmente.
    flush(remote, using=using, limit=1)
    # Si otra venta pendiente perdió conexión, ésta también debe quedar
    # proyectada y durable aunque no se envíe aún fuera de orden.
    with transaction.atomic(using=using):
        LocalNode.objects.using(using).select_for_update().get(pk=state.node_id)
        command = LocalCommand.objects.using(using).get(pk=operation_id)
        state.refresh_from_db(using=using)
        if command.state == "intent":
            if LocalCommand.objects.using(using).filter(node_id=state.node_id, state="conflict").exists():
                command.state = "conflict"
                command.local_result = {**command.local_result, "error": "Una venta anterior requiere revisión; conserva esta referencia y no repitas el cobro."}
                command.save(using=using, update_fields=["state", "local_result"])
                return public_operation(command)
            project(command, state, using)
            command.state = "pending"
            command.save(using=using, update_fields=["state", "local_result"])
        return public_operation(command)


def sync_cycle(remote, *, local_user_id, using="default"):
    """Subir antes de descargar, conservando la proyección de lo aún pendiente."""
    from .replica import synchronize
    node_id = settings.LOCAL_CONFIG["instance_id"]
    try:
        if LocalSaleSession.objects.using(using).filter(node_id=node_id).exists():
            flush(remote, using=using)
            if LocalSaleSession.objects.using(using).get(node_id=node_id).data.get("closed"):
                return ReplicaState.objects.using(using).get(node_id=node_id)
        state = synchronize(remote, local_user_id=local_user_id, using=using)
        refresh_authorization(remote, local_user_id=local_user_id, using=using)
        return state
    except Exception as exc:
        LocalSaleSession.objects.using(using).filter(node_id=node_id).update(
            online=False, error="No se completó la sincronización; los pendientes siguen guardados.")
        if getattr(exc, "status", None) in (401, 403):
            ReplicaState.objects.using(using).filter(node_id=node_id).update(blocked=True)
        raise
