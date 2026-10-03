"""API de dominio del piloto; nube primero, efectivo, sin SQL generado por clientes."""
import hashlib
import json
import secrets
from datetime import timedelta
from uuid import UUID

from django.contrib.auth import authenticate
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from mainApp.models import EquipoHibrido, SesionHibrida, OperacionHibrida, Producto, Inventario, TurnoCaja
from mainApp.permissions import user_can_access_url_name, user_can_change_sale, _load_permission_state
from .feature_flags import HYBRID_POS_FEATURE, TURN_REQUIRED_FEATURE, is_feature_enabled
from pos_shared.protocol import PROTOCOL, ProtocolError, amount, fingerprint, price_cart

SALT = "nova.hybrid.quote.v1"
SESSION_HOURS = 12
MAX_CATALOG = 25000


class HybridError(ProtocolError):
    def __init__(self, message, code="invalid", status=409):
        super().__init__(message)
        self.code, self.status = code, status


def digest(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def require_enabled():
    if not is_feature_enabled(HYBRID_POS_FEATURE, fresh=True):
        raise HybridError("El piloto híbrido está desactivado. No se borraron operaciones locales.", "disabled", 403)


def identifier(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise HybridError("Identificador inválido.", "invalid", 400) from None


def assert_turn_released(turn_id):
    # Antes de 0044 el POS normal debe seguir funcionando.
    if "sesiones_hibridas" not in connection.introspection.table_names():
        return
    if SesionHibrida.objects.filter(turno_id=turn_id, liberada_en__isnull=True).exists():
        raise ValidationError("Este turno tiene una sesión híbrida. En el equipo instalado, sincroniza y pulsa «Finalizar sesión» antes de cerrar caja.")


def create_device(*, actor, point, name):
    secret = secrets.token_urlsafe(32)
    device = EquipoHibrido.objects.create(
        nombre=name, punto=point, creado_por=actor,
        enlace_hash=digest(secret), enlace_vence=timezone.now() + timedelta(minutes=15),
    )
    return device, f"{device.pk}.{secret}"


@transaction.atomic
def enroll(data):
    require_enabled()
    try:
        device_id, code = str(data.get("code", "")).split(".", 1)
        device = EquipoHibrido.objects.select_for_update().get(pk=identifier(device_id), activo=True)
    except (ValueError, EquipoHibrido.DoesNotExist):
        raise HybridError("Código de vinculación inválido o vencido.", "enrollment", 403) from None
    secret = data.get("secret", "")
    if (not isinstance(secret, str) or len(secret) != 64 or any(c not in "0123456789abcdef" for c in secret)
            or device.enlace_vence < timezone.now() or not secrets.compare_digest(device.enlace_hash, digest(code))):
        raise HybridError("Código de vinculación inválido o vencido.", "enrollment", 403)
    hashed = digest(secret)
    if device.token_hash and not secrets.compare_digest(device.token_hash, hashed):
        raise HybridError("Este equipo ya se vinculó; el código no permite reemplazar sus credenciales.", "enrollment", 403)
    device.token_hash, device.visto_en = hashed, timezone.now()
    device.save(update_fields=["token_hash", "visto_en"])
    return {"device_id": str(device.pk), "name": device.nombre, "point": device.punto.nombre}


def authenticate_device(header):
    try:
        prefix, credential = str(header).split(" ", 1)
        device_id, secret = credential.split(".", 1)
        if prefix != "Bearer" or len(secret) != 64:
            raise ValueError
        device = EquipoHibrido.objects.select_related("punto__sucursalid").get(pk=identifier(device_id))
        if not device.token_hash or not secrets.compare_digest(device.token_hash, digest(secret)):
            raise ValueError
    except (ValueError, EquipoHibrido.DoesNotExist, HybridError):
        raise HybridError("Credenciales del equipo inválidas.", "authentication", 401) from None
    device._authenticated_hash = device.token_hash
    return device


def locked_device(original):
    device = EquipoHibrido.objects.select_for_update(of=("self",)).select_related("punto__sucursalid").get(pk=original.pk)
    # Una petición pudo autenticarse justo antes de retirar el equipo. Volver
    # a comprobar dentro del bloqueo impide usar ese permiso ya revocado.
    checked_hash = getattr(original, "_authenticated_hash", None)
    if checked_hash is not None and (not device.token_hash or not secrets.compare_digest(checked_hash, device.token_hash)):
        raise HybridError("El acceso de este equipo fue reemplazado. No reintentes desde la instalación anterior.", "authentication", 401)
    return device


def require_device(device):
    require_enabled()
    if not device.activo:
        raise HybridError("El equipo fue desactivado. Conserva los pendientes y contacta al Web Master.", "revoked", 403)
    from mainApp.models import RecuperacionHibrida
    if RecuperacionHibrida.objects.filter(equipo=device, estado__in=["issued", "review", "approved"]).exists():
        raise HybridError("El equipo está en recuperación autorizada. No uses la instalación anterior.", "recovery", 403)


def require_user(user, device):
    user.refresh_from_db()
    _load_permission_state(user, fresh=True)
    employee = getattr(user, "empleado", None)
    if (not user.is_active or not user_can_access_url_name(user, "generar_venta") or not employee
            or employee.sucursalid_id != device.punto.sucursalid_id):
        raise HybridError("El usuario no tiene permiso para vender en la sucursal de este equipo.", "permission", 403)


def session_payload(session):
    from .hybrid_payments import payment_methods
    from .hybrid_expenses import expense_methods, expense_concepts
    from .hybrid_replica import user_scope
    result = {"session_id": str(session.pk), "user": session.usuario.nombreusuario,
            "user_id": session.usuario_id, "branch_id": session.equipo.punto.sucursalid_id,
            "point_id": session.equipo.punto_id,
            "branch": session.equipo.punto.sucursalid.nombre, "point": session.equipo.punto.nombre,
            "turn_id": session.turno_id, "expires_at": session.vence_en.isoformat(),
            "issued_at": session.creada_en.isoformat(), "sequence": session.secuencia}
    result["payment_methods"] = payment_methods(session)
    result["expense_methods"] = expense_methods(session)
    result["expense_concepts"] = expense_concepts(session)
    result["reference_scope"] = user_scope(session.equipo, session.usuario)
    result["operation_permissions"] = {
        "expense": user_can_access_url_name(session.usuario, "registrar_egreso"),
        "return": user_can_change_sale(session.usuario),
        "turn.close": user_can_access_url_name(session.usuario, "turno_caja_cerrar"),
    }
    if session.turno_id:
        turn = session.turno
        result["turn_snapshot"] = {"id": turn.pk, "state": turn.estado,
                                   "base": str(turn.saldo_apertura_efectivo), "point": result["point"]}
    return result


@transaction.atomic
def start_session(device, data):
    device = locked_device(device)
    require_device(device)
    user = authenticate(username=str(data.get("username", ""))[:150], password=str(data.get("password", ""))[:256])
    if user is None:
        raise HybridError("Usuario o contraseña incorrectos.", "authentication", 401)
    require_user(user, device)
    requested_id = identifier(data.get("session_id"))
    old = SesionHibrida.objects.filter(equipo=device, liberada_en__isnull=True).first()
    if old:
        if old.pk == requested_id and old.usuario_id == user.pk:
            return session_payload(old)
        raise HybridError("Finaliza la sesión anterior en el equipo antes de iniciar otra.", "session_open")
    turn = None
    if is_feature_enabled(TURN_REQUIRED_FEATURE, fresh=True):
        turn = TurnoCaja.objects.select_for_update().filter(cajero=user, puntopago=device.punto, estado="ABIERTO").first()
        if not turn:
            raise HybridError("Primero abre tu turno en el POS conectado. El piloto no abre turnos offline.", "turn_required")
    session = SesionHibrida.objects.create(id=requested_id, equipo=device, usuario=user, turno=turn,
                                         vence_en=timezone.now() + timedelta(hours=SESSION_HOURS))
    return session_payload(session)


def load_session(device, session_id, *, offline_pending=False):
    try:
        session = SesionHibrida.objects.select_related("usuario", "equipo__punto__sucursalid").get(pk=identifier(session_id), equipo=device)
    except SesionHibrida.DoesNotExist:
        raise HybridError("Sesión no encontrada para este equipo.", "session", 403) from None
    if session.liberada_en or (not offline_pending and timezone.now() >= session.vence_en):
        raise HybridError("La autorización offline terminó. Sincroniza los pendientes y finaliza la sesión.", "session_expired", 403)
    return session


@transaction.atomic
def catalog(device, data):
    device = locked_device(device)
    require_device(device)
    session = load_session(device, data.get("session_id"))
    require_user(session.usuario, device)
    known = data.get("known", {})
    if not isinstance(known, dict) or len(known) > MAX_CATALOG or any(not str(k).isdigit() or not isinstance(v, str) or len(v) != 32 for k, v in known.items()):
        raise HybridError("Manifiesto de catálogo inválido.", "catalog", 400)
    rows = list(Producto.objects.filter(tipo_ptm__isnull=True).order_by("pk").values("pk", "nombre", "precio", "codigo_de_barras")[:MAX_CATALOG + 1])
    if len(rows) > MAX_CATALOG:
        raise HybridError("El catálogo supera el límite del piloto; requiere paginación ampliada.", "catalog_limit", 409)
    stocks = dict(Inventario.objects.filter(sucursalid_id=device.punto.sucursalid_id).values_list("productoid_id", "cantidad"))
    updates, ids, count = [], set(), 0
    for row in rows:
        pid = str(row["pk"])
        ids.add(pid)
        value = {"id": row["pk"], "name": row["nombre"], "price": str(row["precio"]),
                 "barcode": row["codigo_de_barras"] or "", "stock": stocks.get(row["pk"], 0)}
        checksum = fingerprint(value)[:32]
        if known.get(pid) != checksum:
            count += 1
            if len(updates) < 500:
                value["digest"] = checksum
                value["quote"] = signing.dumps({"session": str(session.pk), "id": row["pk"],
                    "name": row["nombre"], "price": str(row["precio"])}, salt=SALT, compress=True)
                updates.append(value)
    EquipoHibrido.objects.filter(pk=device.pk).update(visto_en=timezone.now())
    return {"updates": updates, "deleted": sorted(set(known) - ids), "more": count > len(updates),
            "session": session_payload(session),
            "server_time": timezone.now().isoformat()}


@transaction.atomic
def accept_sale(device, data, *, recovery=None):
    # Una transacción contiene venta, inventario, caja y recibo de idempotencia.
    device = locked_device(device)
    operation_id = identifier(data.get("operation_id"))
    payload_hash = fingerprint(data)
    previous = OperacionHibrida.objects.select_related("sesion").filter(pk=operation_id).first()
    if previous:
        if previous.sesion.equipo_id != device.pk or previous.huella != payload_hash:
            raise HybridError("El identificador ya pertenece a otra operación. No se duplicó la venta.", "idempotency")
        return previous.respuesta
    if recovery is None:
        require_device(device)
    else:
        require_enabled()
        if recovery.equipo_id != device.pk or recovery.estado != "approved" or not device.activo:
            raise HybridError("La recuperación no está aprobada.", "recovery", 403)
    session = load_session(device, data.get("session_id"), offline_pending=True)
    require_user(session.usuario, device)
    # El escritor web toma los bloqueos medio -> configuración -> turno. No
    # anticipar el bloqueo del turno aquí: invertir ese orden causa deadlocks.
    if session.turno_id and not TurnoCaja.objects.filter(pk=session.turno_id, estado="ABIERTO").exists():
        raise HybridError("El turno ya no está abierto. Conserva la operación para revisión.", "turn_closed")
    if type(data.get("protocol")) is not int or data["protocol"] != PROTOCOL or type(data.get("sequence")) is not int or data["sequence"] != session.secuencia + 1:
        raise HybridError("Falta sincronizar una operación anterior o la versión no es compatible.", "sequence")
    occurred = parse_datetime(str(data.get("occurred_at", "")))
    # Los relojes de dos equipos no coinciden al microsegundo. El mismo margen
    # de 2 minutos admitido hacia el futuro cubre el arranque con reloj atrasado.
    # No ampliar la vigencia: una fecha apenas anterior se fija al inicio
    # autorizado. El hash idempotente conserva el payload original del cliente.
    clock_margin = timedelta(minutes=2)
    if occurred is None or timezone.is_naive(occurred) or not session.creada_en - clock_margin <= occurred <= session.vence_en or occurred > timezone.now() + clock_margin:
        raise HybridError("La fecha queda fuera de la autorización del equipo. La operación necesita revisión.", "clock")
    occurred = max(occurred, session.creada_en)
    items = data.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise HybridError("Carrito inválido.", "cart", 400)
    products = {}
    for item in items:
        try:
            quote = signing.loads(item.get("quote", ""), salt=SALT)
            if quote["session"] != str(session.pk) or quote["id"] != item.get("id"):
                raise ValueError
            products[quote["id"]] = quote
        except (signing.BadSignature, ValueError, TypeError, KeyError, AttributeError):
            raise HybridError("Un precio no tiene una autorización válida del servidor.", "quote", 409) from None
    if Producto.objects.filter(pk__in=products, tipo_ptm__isnull=True).count() != len(products):
        raise HybridError("Un producto fue retirado o convertido en PTM. Revisa la venta pendiente.", "product")
    details, total = price_cart(items, products)
    if "payments" in data:
        from .hybrid_payments import validate_payments
        payments, received, change = validate_payments(session, data["payments"], total, data.get("cash_received"))
    else:
        received = amount(data.get("cash_received"))
        if received < total:
            raise HybridError("El efectivo recibido no cubre el total.", "cash", 400)
        payments, change = [{"medio_pago": "efectivo", "monto": total}], received - total
    from mainApp.views import GenerarVentaView
    result = GenerarVentaView._crear_venta_ultra_fast(
        session.usuario, device.punto.sucursalid, device.punto, None,
        payments, details, total, received,
        turno=session.turno_id, occurred_at=occurred,
    )
    result = json.loads(result.content)
    if not result.get("success"):
        # También revierte cualquier fallo posterior a persistir la venta en
        # la función web; jamás confirmar sin el recibo idempotente asociado.
        raise HybridError(result.get("error", "No se pudo registrar la venta."), "sale")
    manual = any(row["medio_pago"] != "efectivo" for row in payments)
    receipt = result.get("receipt_text", "")
    if len(payments) > 1 and received:
        receipt += f"\nEFECTIVO RECIBIDO: $ {received}\nCAMBIO: $ {change}\n"
    if manual:
        receipt += "\nMedios electrónicos registrados por el cajero; sin verificación bancaria automática.\n"
    response = {"operation_id": str(operation_id), "sale_id": result["venta_id"], "total": str(total),
                "change": str(change), "status": "accepted", "receipt_text": receipt,
                "payment_verification": "manual_not_verified" if manual else "cash"}
    OperacionHibrida.objects.create(id=operation_id, sesion=session, secuencia=data["sequence"], huella=payload_hash,
        venta_id=result["venta_id"], ocurrida_en=occurred, respuesta=response)
    session.secuencia = data["sequence"]
    session.save(update_fields=["secuencia"])
    device.visto_en = timezone.now()
    device.save(update_fields=["visto_en"])
    return response


@transaction.atomic
def release_session(device, data):
    device = locked_device(device)
    require_device(device)
    session = SesionHibrida.objects.filter(pk=identifier(data.get("session_id")), equipo=device).first()
    if not session or type(data.get("sequence")) is not int or data["sequence"] != session.secuencia:
        raise HybridError("Todavía faltan operaciones por sincronizar. No se liberó la sesión.", "pending")
    if not session.liberada_en:
        session.liberada_en = timezone.now()
        session.save(update_fields=["liberada_en"])
    return {"status": "released", "session_id": str(session.pk)}
