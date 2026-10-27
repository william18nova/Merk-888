"""Allowlisted original-page expenses, refunds and close declarations.

No arbitrary SQL/form replay. Offline events are signed, ordered and immutable;
cloud changes and their UUID receipt commit together. Online requests also get
a durable intent, so an unanswered cloud POST cannot become an offline copy.
"""
import json
from decimal import Decimal, InvalidOperation
from functools import wraps
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import connections, transaction, DatabaseError
from django.http import JsonResponse, HttpResponseRedirect, HttpRequest, QueryDict
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.dateparse import parse_datetime

from mainApp.models import (Usuario, SolicitudPOS, TurnoCaja, Venta, DetalleVenta,
    CambioDevolucion, Inventario, MetodoPago, ConceptoEgreso, Egreso, ReintegroVenta)
from mainApp.permissions import user_can_access_url_name, user_can_change_sale, _load_permission_state
from mainApp.services.pos_request_receipts import HEADER, request_fingerprint, lock_operation, failure
from .integrated_auth import authorized_identity, cloud_fingerprint
from .integrated_journal import operation_lock
from .integrated_offline_sales import SaleError, seal
from .integrated_policy import CLOUD_CONNECT_TIMEOUT
from .models import LocalCommand, LocalNode, ReplicaState, ReplicaRow
from .request_context import offline_request
from pos_shared.replica import manifest

ONLINE_KIND = "integrated.business.request.v1"
KINDS = {action: "integrated." + action + ".offline.v1" for action in
    ("expense", "refund", "open", "close_begin", "close")}
PERMISSION = {"expense": "registrar_egreso", "refund": "ver_venta",
    "open": "turno_recuperar_o_iniciar", "close_begin": "turno_caja_iniciar_cierre", "close": "turno_caja_cerrar"}


def allowed(actor, action):
    return bool(actor.is_active and (user_can_change_sale(actor) if action == "refund"
        else user_can_access_url_name(actor, PERMISSION[action])))


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount < Decimal("1000000000000") or amount != amount.quantize(Decimal(".01")):
            raise ValueError
        return amount
    except (InvalidOperation, TypeError, ValueError):
        raise SaleError("Escribe un valor válido, sin negativos y con máximo dos decimales.") from None


def method(code):
    if code == "nequi":
        raise SaleError("Nequi requiere conexión; no está habilitado para nuevas operaciones offline.")
    row = MetodoPago.objects.filter(pk=code, activo=True).first()
    if not row:
        raise SaleError("El medio de pago no está disponible en la copia autorizada.")
    return row


def return_items(sale, items):
    details = {row.pk: row for row in DetalleVenta.objects.select_for_update().filter(ventaid=sale)}
    result, seen = [], set()
    for item in items:
        detail = details.get(item["detail_id"])
        quantity = item["quantity"]
        if not detail or detail.pk in seen or type(quantity) is not int or not 1 <= quantity <= detail.cantidad:
            raise SaleError("Una cantidad ya no está disponible para devolver. Revisa la venta; no repitas la entrega de dinero.")
        if detail.productoid.tipo_ptm:
            raise SaleError("Las operaciones PTM necesitan conexión.")
        seen.add(detail.pk)
        result.append({"detalle": detail, "cantidad": quantity})
    if not result:
        raise SaleError("Selecciona al menos un producto para devolver.")
    return result


def build(request, action, state, kwargs):
    from mainApp.forms import RegistrarEgresoForm
    from mainApp.services.payment_methods import payment_method_options
    from mainApp.services.expense_tax import expense_amounts, check_expected_tax
    data = state.scope.get("offline_business")
    if not data or data.get("version") != 1:
        raise SaleError("Actualiza la copia con internet antes de usar pagos, devoluciones o cierres offline.")
    if action == "open":
        from .integrated_turns import build_open
        return build_open(request, state)
    if action == "refund" and state.scope.get("business_refresh_required"):
        raise SaleError("La última devolución online aún no se reflejó en la copia. Espera su actualización antes de devolver otra vez.")
    if action == "expense":
        form = RegistrarEgresoForm(request.POST, payment_methods=payment_method_options(active_only=True))
        if not form.is_valid():
            raise SaleError("Revisa el concepto, el valor y el medio del pago.")
        values = form.cleaned_data
        row = method(values["medio_pago"])
        enabled = row.aplica_4xmil_egresos
        expected = values.get("impuesto_esperado")
        check_expected_tax(None if not expected else expected == "1", enabled)
        base, tax, total = expense_amounts(values["monto"], enabled)
        return {"concept": values["concepto"], "method": row.pk, "base": str(base),
            "tax_enabled": enabled, "tax": str(tax), "total": str(total)}
    if action == "refund":
        from mainApp.views import DevolucionFormSet, ReintegroMixtoFormSet, VentaDetailView
        sale_id = kwargs["venta_id"]
        if sale_id not in {sale["ventaid"] for sale in data["sales"]}:
            raise SaleError("Esta venta no está en la copia autorizada. Conéctate para consultarla y devolverla.")
        sale = Venta.objects.select_for_update().get(pk=sale_id)
        if request.POST.get("mediopago", sale.mediopago) != sale.mediopago:
            raise SaleError("Sin conexión puedes devolver productos, pero no cambiar el pago original de una venta.")
        form = DevolucionFormSet(request.POST, prefix="dev")
        refunds = ReintegroMixtoFormSet(request.POST, prefix="reint")
        if not form.is_valid():
            raise SaleError("Revisa las cantidades a devolver.")
        items = [{"detail_id": row["detalle_id"], "quantity": row["devolver"]}
            for row in form.cleaned_data if row.get("devolver", 0) > 0]
        lines = return_items(sale, items)
        total = CambioDevolucion.calcular_total_devolucion(sale, lines)
        ok, error, mapping = VentaDetailView()._validar_reintegro_mixto(sale, refunds, total,
            active_codes=set(MetodoPago.objects.filter(activo=True).values_list("codigo", flat=True)))
        if not ok:
            raise SaleError(error)
        for code in mapping:
            method(code)
        return {"sale_id": sale.pk, "items": items, "total": str(total),
            "refunds": {key: str(value) for key, value in mapping.items()}}
    try:
        turn_id = int(request.POST.get("turno_id", ""))
    except (ValueError, TypeError):
        raise SaleError("Selecciona un turno válido.") from None
    if not data.get("turn") or turn_id != data["turn"]["id"]:
        raise SaleError("Ese turno no está autorizado en este equipo.")
    if data.get("has_ptm") or data.get("has_ptm_bag"):
        raise SaleError("Este turno contiene PTM: debe cerrarse con conexión para conciliar sus transacciones.")
    turn = TurnoCaja.objects.select_for_update().get(pk=turn_id, cajero_id=request.user.pk)
    if turn.estado not in ({"ABIERTO", "CIERRE"} if action == "close_begin" else {"CIERRE"}):
        raise SaleError("El turno no está en el paso correcto para este cierre.")
    if action == "close_begin":
        return {"turn_id": turn.pk}
    try:
        values = json.loads(request.POST.get("medios_json", "[]"))
        ptm = int(request.POST.get("ptm_transacciones") or 0)
    except (ValueError, TypeError):
        raise SaleError("Revisa los medios y el conteo de PTM.") from None
    if ptm != 0 or not isinstance(values, list) or len(values) > 50:
        raise SaleError("PTM no se puede conciliar sin conexión.")
    seen, normalized = set(), []
    for row in values:
        if not isinstance(row, dict) or set(row) != {"metodo", "contado"}:
            raise SaleError("El conteo de medios no es válido.")
        code, amount = row["metodo"], money(row["contado"])
        if code in seen or code in {"efectivo", "facturas_pagadas"}:
            raise SaleError("Hay un medio repetido o no permitido en el conteo.")
        if code == "nequi" and amount == 0:
            continue  # Existing cloud-confirmed Nequi is reconciled on upload.
        method(code)
        seen.add(code)
        normalized.append({"metodo": code, "contado": str(amount)})
    from mainApp.services.close_payments import validate as validate_close_payments
    from mainApp.services.close_progress import validate_final
    try:
        bills, details = validate_close_payments(request.POST.get("facturas_pagadas", "0"), request.POST.get("pagos_detalle"))
        validate_final(turn, bills, details, money(request.POST.get("efectivo_entregado", 0)))
    except ValueError as exc:
        raise SaleError(str(exc)) from None
    return {"turn_id": turn.pk, "cash": str(money(request.POST.get("efectivo_entregado", 0))),
        "bills": str(bills), "bills_details": details, "methods": normalized}


def project(action, values, request, state, occurred):
    # Domain models use the whole-request default alias, which is now bound to
    # offline. Do not mix an explicitly `.using("offline")` model instance with
    # default-alias instances: Django rightly rejects cross-alias relations.
    actor = Usuario.objects.get(pk=request.user.pk)
    if action == "expense":
        concept, _ = ConceptoEgreso.objects.get_or_create(nombre=values["concept"], defaults={"creado_por": actor})
        expense = Egreso.objects.create(concepto=concept, monto=values["total"],
            impuesto_4xmil=values["tax"], aplica_4xmil=values["tax_enabled"], medio_pago=values["method"],
            registrado_por=actor, registrado_por_nombre=actor.get_username()[:160])
        Egreso.objects.filter(pk=expense.pk).update(creado_en=occurred)
    elif action == "refund":
        sale = Venta.objects.select_for_update().get(pk=values["sale_id"])
        lines = return_items(sale, values["items"])
        CambioDevolucion.registrar_devolucion(sale, lines, reintegro_map=values["refunds"],
            registrado_por=actor, turno_requerido=True)
        for line in lines:
            stock = Inventario.objects.get(productoid=line["detalle"].productoid, sucursalid=sale.sucursalid)
            row = ReplicaRow.objects.using("offline").get(node=state.node, entity="stock", source_id=str(stock.pk))
            row.values = {**row.values, "cantidad": stock.cantidad}
            row.save(using="offline", update_fields=["values"])
        state.manifest = manifest([{"entity": r.entity, "id": r.source_id, "values": r.values}
            for r in ReplicaRow.objects.using("offline").filter(node=state.node)])
    elif action == "open":
        from .integrated_turns import project_open
        project_open(values, state, occurred)
    else:
        changes = {"estado": "CIERRE", "cierre_iniciado": occurred} if action == "close_begin" else {"estado": "CERRADO", "fin": occurred}
        TurnoCaja.objects.filter(pk=values["turn_id"]).update(**changes)
    state.scope = {**state.scope, "refresh_required": True}
    state.save(using="offline", update_fields=["scope", "manifest"])


def turn_payload(turn, user=None):
    """No speculative final balances and no writes from GET close pages."""
    from mainApp.services.payment_methods import payment_method_options
    return {"success": True, "turno_id": turn.pk, "estado": turn.estado,
        "inicio": turn.inicio.isoformat(), "cierre_iniciado": turn.cierre_iniciado.isoformat() if turn.cierre_iniciado else None,
        "base": float(turn.saldo_apertura_efectivo), "hide_bd_cols": True,
        "puntopago": {"id": turn.puntopago_id, "nombre": turn.puntopago.nombre},
        "cajero": {"id": turn.cajero_id, "nombreusuario": turn.cajero.get_username()},
        "medios": [{"metodo": row["code"], "label": row["label"], "esperado": 0, "contado": 0,
            "auto_confirmado": 0} for row in payment_method_options(active_only=True)],
        "auto_confirmados": {}, "manuales_sin_api": {}, "offline_snapshot": True}


def render_result(request, result, operation_id):
    if "json" in result:
        response = JsonResponse(result["json"], status=result.get("status", 200))
    else:
        url = result["redirect"]
        if request.headers.get("X-Nova-Business") == "1":
            response = JsonResponse({"success": True, "redirect": url})
        else:
            response = HttpResponseRedirect(url)
    response[HEADER] = str(operation_id)
    response["Cache-Control"] = "no-store"
    return response


def save(request, action, operation_id, kwargs):
    with transaction.atomic(using="offline"):
        node = LocalNode.objects.using("offline").select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        state = ReplicaState.objects.using("offline").get(node=node)
        old = LocalCommand.objects.using("offline").filter(pk=operation_id).first()
        if old and old.kind == KINDS[action]:
            return replay(request, old)
        if old and (old.kind != ONLINE_KIND or old.actor_id != request.user.pk or old.node_id != node.pk
                or old.payload.get("action") != action or old.payload.get("source") != cloud_fingerprint()
                or old.state != "accepted" or old.local_result.get("outcome") not in {"absent", "rejected"}):
            raise SaleError("Esta operación ya se intentó en la nube. Espera su confirmación; no la repitas.")
        now = timezone.now()
        if (not authorized_identity(request.user) or not allowed(request.user, action) or state.blocked
                or not state.active_id or state.local_user_id != request.user.pk or state.device_id != node.pk
                or state.server != "django-db:" + cloud_fingerprint() or not state.expires_at or state.expires_at <= now):
            raise SaleError("La autorización offline no está vigente para esta operación.")
        last = LocalCommand.objects.using("offline").filter(node=node).order_by("-sequence").first()
        if state.synchronized_at > now or (last and last.created_at > now):
            raise SaleError("La hora del equipo retrocedió. Corrige el reloj antes de continuar.")
        if LocalCommand.objects.using("offline").filter(node=node, state="conflict").exists():
            raise SaleError("Revisa primero la operación pendiente de conciliación.")
        granted_turn = state.scope.get("offline_sale", {}).get("turn_id")
        if action == "refund" and granted_turn and not TurnoCaja.objects.filter(pk=granted_turn, estado="ABIERTO").exists():
            raise SaleError("El conteo ya comenzó o terminó; actualiza la copia antes de registrar otra devolución.")
        values = build(request, action, state, kwargs)
        if action == "open":
            values["operation_id"] = str(operation_id)
        payload = {"action": action, "values": values, "source": cloud_fingerprint(), "actor_id": request.user.pk,
            "operation_id": str(operation_id), "node_id": str(node.pk), "branch_id": state.scope["branch_id"],
            "point_id": state.scope["point_id"], "turn_id": state.scope["offline_sale"]["turn_id"],
            "turn_operation": state.scope["offline_sale"].get("turn_operation"),
            "occurred_at": now.isoformat(), "authorized_until": state.expires_at.isoformat()}
        project(action, values, request, state, now)
        if action in {"open", "close_begin"}:
            result = {"json": {**turn_payload(TurnoCaja.objects.get(pk=values["turn_id"])), "offline_saved": True}}
            if action == "open":
                result["json"]["msg"] = "Turno abierto en este equipo. Pendiente de sincronizar con la nube."
        elif action == "close":
            result = {"json": {"success": True, "offline_saved": True, "turno_id": values["turn_id"],
                "msg": "Conteo guardado en este equipo. El cuadre definitivo se calculará al sincronizar.",
                "pending_url": reverse("integrated_sync")}}
        else:
            url = reverse("registrar_egreso") if action == "expense" else reverse("ver_venta", args=[values["sale_id"]])
            result = {"redirect": url + "?nova_ack=" + str(operation_id)}
            if hasattr(request, "_messages"):
                messages.success(request, "Guardado en este equipo. Pendiente de enviar a la nube; no repitas la operación.")
        node.sequence += 1
        node.save(using="offline", update_fields=["sequence"])
        LocalCommand.objects.using("offline").update_or_create(pk=operation_id, defaults={"node": node,
            "actor_id": request.user.pk, "sequence": node.sequence, "kind": KINDS[action], "state": "pending",
            "fingerprint": request_fingerprint(request), "payload": payload,
            "local_result": {"outcome": "saved_offline", "seal": seal(payload), "response": result}})
    return render_result(request, result, operation_id)


def replay(request, command):
    if (command.actor_id != request.user.pk or str(command.node_id) != str(settings.LOCAL_CONFIG["instance_id"])
            or command.fingerprint != request_fingerprint(request) or command.payload.get("source") != cloud_fingerprint()):
        raise SaleError("Esa referencia pertenece a otra operación. No se registró otra vez.")
    return render_result(request, command.local_result["response"], command.pk)


def protect(action):
    def decorate(view):
        @wraps(view)
        def dispatch(self, request, *args, **kwargs):
            if not getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
                return view(self, request, *args, **kwargs)
            try:
                try:
                    operation_id = UUID(request.headers.get(HEADER) or request.POST.get("nova_operation_id", ""))
                except (ValueError, TypeError, AttributeError):
                    raise SaleError("Actualiza esta página; falta una referencia válida para proteger la operación.") from None
                if operation_id.version != 4 or not allowed(request.user, action):
                    raise SaleError("Actualiza la página y comprueba tus permisos antes de continuar.")
                with operation_lock(operation_id) as locked:
                    if not locked:
                        raise SaleError("La operación se está verificando; espera sin repetir la entrega de dinero.")
                    if offline_request():
                        if action == "open":
                            from .integrated_discounts import verify_open_password
                            verify_open_password(request, operation_id)
                        return save(request, action, operation_id, kwargs)
                    return online(request, action, operation_id, lambda: view(self, request, *args, **kwargs))
            except SaleError as exc:
                return failure(str(exc), "business_validation")
            except DatabaseError:
                return failure("No se confirmó la operación. Conserva el formulario: se verificará con la misma referencia.", "business_uncertain", 503)
        return dispatch
    return decorate


def online(request, action, operation_id, execute):
    digest = request_fingerprint(request)
    with transaction.atomic(using="offline"):
        node = LocalNode.objects.using("offline").select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        state = ReplicaState.objects.using("offline").get(node=node)
        if state.local_user_id != request.user.pk or state.server != "django-db:" + cloud_fingerprint():
            raise SaleError("La instalación no está vinculada a este usuario y nube.")
        command = LocalCommand.objects.using("offline").filter(pk=operation_id).first()
        if command:
            if command.kind == KINDS[action] or (command.state == "accepted" and command.local_result.get("outcome") == "confirmed"):
                return replay(request, command)
            retryable = command.state == "accepted" and command.local_result.get("outcome") in {"absent", "rejected"}
            if (command.kind != ONLINE_KIND or command.actor_id != request.user.pk or command.node_id != node.pk
                    or (command.fingerprint != digest and not retryable) or command.payload != {"source": cloud_fingerprint(), "action": action}):
                raise SaleError("La referencia pendiente no coincide. No se registró otra operación.")
            if retryable:
                command.fingerprint, command.state, command.local_result = digest, "intent", {}
                command.save(using="offline", update_fields=["fingerprint", "state", "local_result"])
        else:
            node.sequence += 1
            node.save(using="offline", update_fields=["sequence"])
            command = LocalCommand.objects.using("offline").create(pk=operation_id, node=node, actor_id=request.user.pk,
                sequence=node.sequence, kind=ONLINE_KIND, fingerprint=digest,
                payload={"source": cloud_fingerprint(), "action": action}, state="intent")
    with transaction.atomic():
        lock_operation(operation_id)
        receipt = SolicitudPOS.objects.filter(pk=operation_id).first()
        if receipt:
            validate_receipt(receipt, command, action)
            result = receipt.respuesta
        else:
            response = execute()
            if response.status_code >= 400:
                transaction.set_rollback(True)
                LocalCommand.objects.using("offline").filter(pk=command.pk).update(state="accepted", local_result={"outcome": "rejected"})
                if request.headers.get("X-Nova-Business") == "1" and not response.get("Content-Type", "").startswith("application/json"):
                    return failure("Revisa los datos del formulario. El pago no se registró; puedes corregirlo y reintentar.", "business_rejected", 400)
                return response
            if response.status_code == 302:
                url = response["Location"]
                result = {"redirect": url + ("&" if "?" in url else "?") + "nova_ack=" + str(operation_id)}
            elif response.get("Content-Type", "").startswith("application/json"):
                data = json.loads(response.content)
                if data.get("success") is not True:
                    transaction.set_rollback(True)
                    LocalCommand.objects.using("offline").filter(pk=command.pk).update(state="accepted", local_result={"outcome": "rejected"})
                    return response
                result = {"json": data}
            else:
                raise SaleError("No se recibió una confirmación válida. No se guardó la operación.")
            SolicitudPOS.objects.create(pk=operation_id, usuario=request.user, accion=action, huella=digest, respuesta=result)
    with transaction.atomic(using="offline"):
        LocalCommand.objects.using("offline").filter(pk=command.pk).update(state="accepted",
            local_result={"outcome": "confirmed", "response": result})
        project_online_ack(command, result)
    return render_result(request, result, operation_id)


def project_online_ack(command, result):
    """Invalidate stale refund availability; mirror only confirmed turn phase."""
    with transaction.atomic(using="offline"):
        state = ReplicaState.objects.using("offline").select_for_update().get(node=command.node)
        action = command.payload["action"]
        scope = {**state.scope, "refresh_required": True}
        if action == "refund":
            scope["business_refresh_required"] = True
        data = result.get("json", {})
        if action == "open" and data.get("success") is True:
            # A cloud opening must be downloaded before it authorizes local sales.
            scope["offline_sale"] = {**scope.get("offline_sale", {}), "turn_id": None, "turn_operation": None}
        if action in {"close_begin", "close"} and data.get("success") is True:
            turn_id = data.get("turno_id")
            if turn_id == scope.get("offline_sale", {}).get("turn_id"):
                TurnoCaja.objects.using("offline").filter(pk=turn_id).update(estado="CIERRE" if action == "close_begin" else "CERRADO")
        state.scope = scope
        state.save(using="offline", update_fields=["scope"])


def validate_receipt(receipt, command, action):
    if (receipt.usuario_id != command.actor_id or receipt.accion != action
            or receipt.huella != command.fingerprint or not receipt.respuesta):
        raise SaleError("La confirmación existente no coincide; se requiere revisión.")


def call_close(view, actor, values, *, allow_legacy_payments=False):
    request = HttpRequest()
    request.method, request.user = "POST", actor
    request.POST = QueryDict(mutable=True)
    request.POST.update(values)
    # The event adapter, not the web decorator, owns the UUID and transaction.
    kwargs = {"_allow_legacy_payment_details": True} if allow_legacy_payments else {}
    response = view.post.__wrapped__(view(), request, **kwargs)
    data = json.loads(response.content)
    if response.status_code != 200 or data.get("success") is not True:
        raise SaleError(data.get("error", "El cierre necesita revisión."))
    return data


def execute_event(command):
    payload = command.payload
    action, values = payload["action"], payload["values"]
    occurred = parse_datetime(payload["occurred_at"])
    actor = Usuario.objects.select_related("rolid", "empleado").get(pk=command.actor_id)
    _load_permission_state(actor, fresh=True)
    if not allowed(actor, action) or actor.empleado.sucursalid_id != payload["branch_id"]:
        raise SaleError("El usuario perdió su permiso o cambió de sucursal.")
    from .integrated_turns import resolve_turn, upload_open
    if action == "open":
        return upload_open(command, actor, occurred)
    turn_id = resolve_turn(payload)
    if action == "expense":
        from mainApp.services.expense_tax import expense_amounts
        method(values["method"])
        _, tax, total = expense_amounts(values["base"], values["tax_enabled"])
        if str(tax) != values["tax"] or str(total) != values["total"]:
            raise SaleError("El importe del pago no coincide con su autorización.")
        concept, _ = ConceptoEgreso.objects.get_or_create(nombre=values["concept"], defaults={"creado_por": actor})
        expense = Egreso.objects.create(concepto=concept, monto=total, impuesto_4xmil=tax,
            aplica_4xmil=values["tax_enabled"], medio_pago=values["method"], registrado_por=actor,
            registrado_por_nombre=actor.get_username()[:160])
        Egreso.objects.filter(pk=expense.pk).update(creado_en=occurred)
        return {"success": True, "expense_id": expense.pk, "total": str(total)}
    turn = TurnoCaja.objects.select_for_update().filter(pk=turn_id,
        cajero=actor, puntopago_id=payload["point_id"]).first() if turn_id else None
    if action == "refund":
        # Never charge a refund to a later shift that opened during the outage.
        current = TurnoCaja.objects.filter(cajero=actor, puntopago_id=payload["point_id"], estado="ABIERTO").first()
        if (current.pk if current else None) != turn_id:
            raise SaleError("El turno de la devolución cambió; concilia la salida de dinero antes de reenviar.")
        sale = Venta.objects.select_for_update().get(pk=values["sale_id"],
            empleadoid=actor.empleado, puntopagoid_id=payload["point_id"], sucursalid_id=payload["branch_id"])
        lines = return_items(sale, values["items"])
        if CambioDevolucion.calcular_total_devolucion(sale, lines) != Decimal(values["total"]):
            raise SaleError("Cambió el importe disponible para devolver; se requiere conciliación.")
        for code in values["refunds"]:
            method(code)
        before = list(ReintegroVenta.objects.filter(venta=sale).values_list("pk", flat=True))
        changes = list(sale.cambios.values_list("pk", flat=True))
        CambioDevolucion.registrar_devolucion(sale, lines, reintegro_map=values["refunds"], registrado_por=actor, turno_requerido=True)
        ReintegroVenta.objects.filter(venta=sale).exclude(pk__in=before).update(creado_en=occurred)
        sale.cambios.exclude(pk__in=changes).update(fecha=timezone.localtime(occurred).date())
        return {"success": True, "sale_id": sale.pk, "total": values["total"]}
    if not turn or turn.estado not in {"ABIERTO", "CIERRE"} or occurred < turn.inicio:
        raise SaleError("El turno ya fue cerrado o cambió; revisa el cierre pendiente.")
    from mainApp.services.ptm_cash_pool import bag_for_turn
    if turn.operaciones_ptm.exists() or bag_for_turn(turn):
        raise SaleError("El turno contiene PTM. Requiere conciliación con conexión.")
    from mainApp.views import TurnoCajaIniciarCierreApi, TurnoCajaCerrarApi
    if action == "close_begin":
        result = call_close(TurnoCajaIniciarCierreApi, actor, {"turno_id": str(turn.pk)})
        TurnoCaja.objects.filter(pk=turn.pk).update(cierre_iniciado=occurred)
        return result
    if turn.estado != "CIERRE":
        raise SaleError("Falta conciliar el inicio del cierre.")
    close_values = {"turno_id": str(turn.pk), "efectivo_entregado": values["cash"],
        "facturas_pagadas": values["bills"], "ptm_transacciones": "0", "medios_json": json.dumps(values["methods"])}
    if "bills_details" in values:
        close_values["pagos_detalle"] = json.dumps(values["bills_details"], ensure_ascii=False)
    result = call_close(TurnoCajaCerrarApi, actor, close_values, allow_legacy_payments="bills_details" not in values)
    TurnoCaja.objects.filter(pk=turn.pk).update(fin=occurred)
    return result


def upload(command):
    payload = command.payload
    if (offline_request() or payload.get("source") != cloud_fingerprint()
            or payload.get("operation_id") != str(command.pk) or payload.get("node_id") != str(command.node_id)
            or payload.get("actor_id") != command.actor_id or KINDS.get(payload.get("action")) != command.kind
            or not constant_time_compare(command.local_result.get("seal", ""), seal(payload))):
        raise SaleError("La operación no coincide con su autorización original.")
    occurred, expires = parse_datetime(payload["occurred_at"]), parse_datetime(payload["authorized_until"])
    if not occurred or not expires or occurred > expires or occurred > timezone.now():
        raise SaleError("La fecha de la operación está fuera de la autorización.")
    cloud = connections["default"]
    if cloud.in_atomic_block:
        raise RuntimeError("La sincronización exige una conexión independiente.")
    original = cloud.settings_dict
    cloud.close()
    cloud.settings_dict = {**original, "OPTIONS": {**original.get("OPTIONS", {}),
        "connect_timeout": CLOUD_CONNECT_TIMEOUT, "options": "-c statement_timeout=15000 -c lock_timeout=3000"}}
    try:
        with transaction.atomic():
            lock_operation(command.pk)
            receipt = SolicitudPOS.objects.filter(pk=command.pk).first()
            if receipt:
                validate_receipt(receipt, command, payload["action"])
                return receipt.respuesta
            try:
                result = execute_event(command)
            except (Usuario.DoesNotExist, Venta.DoesNotExist, TurnoCaja.DoesNotExist):
                raise SaleError("El usuario, turno o venta cambió. La operación se conserva para revisión.") from None
            except SaleError:
                raise
            except (ValueError, ValidationError):
                raise SaleError("Las reglas actuales no permiten aplicar ese movimiento. Se conservó para revisión, sin repetir el pago ni la devolución.") from None
            SolicitudPOS.objects.create(pk=command.pk, usuario_id=command.actor_id, accion=payload["action"],
                huella=command.fingerprint, respuesta=result)
            return result
    finally:
        cloud.close()
        cloud.settings_dict = original


def reconcile_online(command):
    if offline_request() or command.payload.get("source") != cloud_fingerprint():
        raise SaleError("La operación pertenece a otra nube.")
    from .integrated_policy import bounded_cloud_connection
    with bounded_cloud_connection(), transaction.atomic():
        lock_operation(command.pk)
        receipt = SolicitudPOS.objects.filter(pk=command.pk).first()
        if receipt:
            validate_receipt(receipt, command, command.payload["action"])
            result = {"outcome": "confirmed", "response": receipt.respuesta}
        else:
            result = {"outcome": "absent"}
    with transaction.atomic(using="offline"):
        LocalCommand.objects.using("offline").filter(pk=command.pk).update(state="accepted", local_result=result)
        if result["outcome"] == "confirmed":
            project_online_ack(command, receipt.respuesta)
