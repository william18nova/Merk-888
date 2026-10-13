"""Bounded offline authorization. No plaintext secret is stored in an event.

Only password verifiers for eligible buyers are copied, never login grants for
those buyers. Special one-use codes must be explicitly reserved online first.
"""
import hashlib
from decimal import Decimal
from datetime import timedelta
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from mainApp.models import Cliente, Empleado, Usuario, AutorizacionDescuentoEspecial
from .models import ReplicaState, LocalCommand, LocalNode
from . import integrated_auth
from .integrated_offline_sales import SaleError


def verifier_id(encoded):
    return hashlib.sha256(encoded.encode()).hexdigest()


def capture(scope):
    from mainApp.views import GenerarVentaView
    result = {}
    if scope["clients"]:
        employees = {}
        for employee in Empleado.objects.select_related("usuarioid__rolid").all():
            doc = GenerarVentaView._normalize_document(employee.numerodocumento)
            if doc:
                employees.setdefault(doc, []).append(employee)
        for client in Cliente.objects.all():
            matches = employees.get(GenerarVentaView._normalize_document(client.numerodocumento), [])
            if len(matches) != 1:
                continue
            employee = matches[0]
            user = employee.usuarioid
            if (not user or not user.is_active or not user.has_usable_password()
                    or user.pk == scope["user_id"] or employee.sucursalid_id != scope["branch_id"]):
                continue
            result[str(client.pk)] = {"kind": "employee", "employee_id": employee.pk, "user_id": user.pk,
                "name": str(employee), "hash": user.password,
                "web_master": GenerarVentaView._empleado_es_web_master(employee)}
        for auth in AutorizacionDescuentoEspecial.objects.select_related("cliente_especial").filter(
                usada_en__isnull=True, revocada_en__isnull=True, bloqueada_en__isnull=True,
                expira_en__gt=timezone.now(), cliente_especial__activo=True):
            from mainApp.services.special_discount import get_offline_reservation
            reservation = get_offline_reservation(auth)
            if reservation == binding(scope):
                result[str(auth.cliente_especial.cliente_id)] = {"kind": "special", "id": auth.pk,
                    "hash": auth.secreto_hash, "expires": auth.expira_en.isoformat(),
                    "reservation": reservation, "authorized_by": auth.generada_por_id}
    scope["offline_discounts"] = result
    scope["offline_sale"]["clients"] += [int(pk) for pk in result]


def binding(scope):
    return {"node_id": str(scope["device_id"]), "actor_id": scope["user_id"], "point_id": scope["point_id"]}


def reserve(authorization, user):
    from mainApp.permissions import is_web_master_role
    from mainApp.services.special_discount import SpecialDiscountError, _ensure_available, reservation_id
    from mainApp.models import SolicitudPOS
    state = ReplicaState.objects.using("offline").filter(node_id=settings.LOCAL_CONFIG["instance_id"],
        server="django-db:" + integrated_auth.cloud_fingerprint(), active_id__isnull=False,
        expires_at__gt=timezone.now(), blocked=False).first()
    if not user.is_active or not is_web_master_role(user) or not state:
        raise SpecialDiscountError("Prepara primero la copia autorizada de este equipo. La reserva requiere un Web Master.")
    with transaction.atomic():
        auth = AutorizacionDescuentoEspecial.objects.select_for_update().get(pk=authorization.pk)
        _ensure_available(auth)
        SolicitudPOS.objects.create(pk=reservation_id(auth), usuario=user, accion="discount_reserve",
            huella=verifier_id(auth.secreto_hash), respuesta=binding(state.scope))
    return auth


def _check_secret(request, operation_id, *, opening=False):
    if LocalCommand.objects.using("offline").filter(pk=operation_id, actor_id=request.user.pk,
            local_result__outcome="saved_offline").exists():
        return  # Original replay still validates its fingerprint.
    error = None
    with transaction.atomic(using="offline"):
        LocalNode.objects.using("offline").select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        state = ReplicaState.objects.using("offline").get(node_id=settings.LOCAL_CONFIG["instance_id"])
        if (not integrated_auth.authorized_identity(request.user) or state.local_user_id != request.user.pk
                or state.blocked or not state.expires_at or state.expires_at <= timezone.now()):
            raise SaleError("Actualiza la autorización offline antes de continuar.")
        if opening:
            encoded = Usuario.objects.using("offline").get(pk=request.user.pk).password
            secret, key = request.POST.get("password", ""), "open:" + str(request.user.pk)
        else:
            row = state.scope.get("offline_discounts", {}).get(str(request.POST.get("cliente_id")))
            if not row:
                if request.POST.get("empleado_password") or request.POST.get("codigo_descuento_merk2888"):
                    raise SaleError("Esta autorización no está preparada en la copia offline.")
                return
            encoded = row["hash"]
            secret = request.POST.get("codigo_descuento_merk2888" if row["kind"] == "special" else "empleado_password", "").strip()
            key = ("special:" + str(row["id"])) if row["kind"] == "special" else "employee:" + str(row["user_id"])
        attempts = dict(state.scope.get("authorization_attempts", {}))
        history = attempts.get(key, {})
        until = parse_datetime(history.get("until", ""))
        if until and until > timezone.now():
            error = "Demasiados intentos. Espera 15 minutos antes de volver a autorizar."
        elif not check_password(secret, encoded):
            count = (history.get("count", 0) if not until else 0) + 1
            attempts[key] = {"count": count, "until": (timezone.now() + timedelta(minutes=15)).isoformat() if count >= 5 else ""}
            error = "Contraseña o clave incorrecta. La operación no se guardó."
        else:
            attempts.pop(key, None)
            request.nova_verified_discount = None if opening else {**row, "client_id": int(request.POST["cliente_id"])}
        state.scope = {**state.scope, "authorization_attempts": attempts}
        state.save(using="offline", update_fields=["scope"])
    # Raise AFTER commit: failed attempts survive rollback of the sale itself.
    if error:
        raise SaleError(error)


def verify_open_password(request, operation_id):
    _check_secret(request, operation_id, opening=True)


def verify_sale_password(request, operation_id):
    if not any(request.POST.get(key) for key in ("cliente_id", "empleado_password", "codigo_descuento_merk2888")):
        return  # Ordinary cash sales retain the existing one-transaction fast path.
    _check_secret(request, operation_id)


def sale_discount(request, state, data, subtotal):
    from mainApp.views import GenerarVentaView, _round_account_peso
    row = state.scope.get("offline_discounts", {}).get(str(data.get("cliente_id")))
    if not row:
        return None, subtotal
    verified = getattr(request, "nova_verified_discount", None)
    if not verified or verified.get("client_id") != data["cliente_id"] or verified["hash"] != row["hash"]:
        raise SaleError("Falta validar la autorización del comprador.")
    proof = {key: value for key, value in row.items() if key != "hash"}
    proof["verifier"] = verifier_id(row["hash"])
    if row["kind"] == "special":
        if parse_datetime(row["expires"]) <= timezone.now() or LocalCommand.objects.using("offline").filter(
                node=state.node, payload__discount__kind="special", payload__discount__id=row["id"]).exists():
            raise SaleError("Esta clave especial venció o ya se usó en este equipo.")
        total = Decimal(0)
    else:
        rate = GenerarVentaView.WEB_MASTER_DISCOUNT_RATE if row["web_master"] else GenerarVentaView.EMPLOYEE_DISCOUNT_RATE
        total = _round_account_peso(subtotal - _round_account_peso(subtotal * rate))
    proof["amount"] = str(subtotal - total)
    return proof, total


def client_flags(results):
    from .request_context import offline_request
    if not offline_request():
        return
    state = ReplicaState.objects.using("offline").filter(node_id=settings.LOCAL_CONFIG["instance_id"]).first()
    grants = state.scope.get("offline_discounts", {}) if state else {}
    for item in results:
        row = grants.get(str(item["id"]))
        if row:
            item.update(is_employee=row["kind"] == "employee", employee_has_user=row["kind"] == "employee",
                employee_name=row.get("name", ""), employee_is_web_master=row.get("web_master", False),
                is_merk2888=row["kind"] == "special")


def lock_reserved_code(client, auth_id, reservation, actor, point, occurred):
    from mainApp.services.special_discount import _ensure_available, is_special_client
    auth = AutorizacionDescuentoEspecial.objects.select_for_update().get(pk=auth_id)
    if (not occurred or not is_special_client(client) or auth.cliente_especial.cliente_id != client.pk
            or reservation.get("actor_id") != actor.pk or reservation.get("point_id") != point.pk
            or occurred < auth.generada_en):
        raise SaleError("La reserva especial no corresponde a esta venta.")
    _ensure_available(auth, now=occurred, offline_reservation=reservation)
    return auth


def validate_upload(payload, client, actor, occurred):
    from mainApp.views import GenerarVentaView
    from mainApp.services.special_discount import is_special_client
    proof = payload.get("discount")
    if not proof:
        if client and (is_special_client(client) or GenerarVentaView._empleado_por_documento_cliente(client)):
            raise SaleError("Cambió la autorización de este cliente. Se requiere revisión.")
        return {}
    subtotal, total = Decimal(payload["subtotal"]), Decimal(payload["total"])
    if subtotal - total != Decimal(proof["amount"]):
        raise SaleError("El descuento no coincide con la autorización original.")
    result = {"descuento_empleado": subtotal - total, "subtotal_original": subtotal}
    if proof["kind"] == "employee":
        employee = GenerarVentaView._empleado_por_documento_cliente(client)
        user = employee.usuarioid if employee else None
        if (not user or not user.is_active or user.pk == actor.pk or user.pk != proof["user_id"]
                or employee.pk != proof["employee_id"] or employee.sucursalid_id != payload["branch_id"]
                or verifier_id(user.password) != proof["verifier"]):
            raise SaleError("Cambió el empleado o su autorización; el descuento se conservó para revisión.")
        amount, expected, free = GenerarVentaView._employee_sale_pricing(employee, subtotal)
        if amount != subtotal - total or expected != total:
            raise SaleError("Cambió el beneficio del empleado. Se requiere revisión.")
        return {**result, "empleado_comprador": employee, "beneficio_web_master": free}
    if proof["kind"] != "special" or total != 0:
        raise SaleError("Tipo de autorización inválido.")
    from mainApp.models import PuntosPago
    auth = lock_reserved_code(client, proof["id"], proof["reservation"], actor,
        PuntosPago.objects.get(pk=payload["point_id"]), occurred)
    if proof["reservation"].get("node_id") != payload["node_id"] or verifier_id(auth.secreto_hash) != proof["verifier"]:
        raise SaleError("La clave fue reservada para otro equipo o cambió.")
    return {**result, "beneficio_merk2888": True, "autorizacion_descuento_id": auth.pk,
        "offline_discount_reservation": proof["reservation"]}
