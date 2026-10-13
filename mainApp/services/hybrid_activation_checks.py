"""Read-only prerequisites for a supervised activation, never an activation itself."""
import unicodedata

from django.db import DatabaseError, connections, transaction


class ActivationCheckError(RuntimeError):
    """Only deliberate, credential-free messages may reach the operator."""


def _name(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).casefold().split())


def database_failure_reason(error):
    # Inspect the driver's message, but never return it (it can contain a DSN).
    text = str(error).lower()
    if any(word in text for word in ("timeout expired", "connection timed out")):
        return "Aiven no respondió a tiempo al abrir la conexión. No se comprobó el usuario ni se descargó una copia."
    if "could not translate host name" in text:
        return "No se pudo resolver el nombre del servidor de la base de datos."
    if "password authentication failed" in text:
        return "El servidor rechazó las credenciales configuradas. No se cambió ninguna contraseña."
    if any(word in text for word in ("certificate verify failed", "ssl error")):
        return "Falló la comprobación TLS de la conexión. Revisa el certificado; no desactives su validación."
    if any(word in text for word in ("connection refused", "network is unreachable", "no route to host")):
        return "No se pudo establecer la conexión TCP con el servidor y puerto configurados."
    if "no pg_hba.conf entry" in text:
        return "El servidor rechazó el acceso de este cliente; revisa su autorización de conexión."
    return "La comprobación de la base de datos falló. Revisa su esquema y permisos antes de activar."


def _inspect(usuario, caja, *, user_id=None, point_id=None):
    from mainApp.models import (ConfiguracionFuncionalidad, MetodoPago, PuntosPago,
                               SolicitudPOS, TurnoCaja, Usuario)
    from mainApp.permissions import _load_permission_state, user_can_access_url_name
    from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE

    connection = connections["default"]
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('solicitudes_pos'), to_regclass('django_migrations')")
        receipts, migrations = cursor.fetchone()
        if not receipts or not migrations:
            raise ActivationCheckError("Falta verificar/aplicar mainApp.0049_solicitudpos en la nube. No se ejecutaron migraciones.")
        cursor.execute("SELECT EXISTS(SELECT 1 FROM django_migrations WHERE app=%s AND name=%s)",
                       ["mainApp", "0049_solicitudpos"])
        if not cursor.fetchone()[0]:
            raise ActivationCheckError("La migración mainApp.0049_solicitudpos no figura aplicada. No se ejecutaron migraciones.")
        cursor.execute("SELECT has_table_privilege(current_user, 'solicitudes_pos', 'SELECT'), "
                       "has_table_privilege(current_user, 'solicitudes_pos', 'INSERT')")
        if not all(cursor.fetchone()):
            raise ActivationCheckError("La conexión necesita SELECT e INSERT sobre solicitudes_pos para confirmar ventas sin duplicarlas.")
    # Validate every field the receipt adapter needs, even if the table is empty.
    columns = ", ".join(connection.ops.quote_name(field.column) for field in SolicitudPOS._meta.concrete_fields)
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {columns} FROM solicitudes_pos LIMIT 0")
    users = Usuario.objects.using("default").select_related("empleado__sucursalid")
    if user_id is not None:
        users = users.filter(pk=user_id)
    matches = []
    for user in users.iterator(chunk_size=200):
        employee = getattr(user, "empleado", None)
        full_name = f"{employee.nombre} {employee.apellido}" if employee else ""
        if user_id is not None or _name(usuario) in {_name(user.nombreusuario), _name(full_name)}:
            matches.append((user, employee, full_name))
    if len(matches) != 1:
        raise ActivationCheckError("No hay una coincidencia única para el usuario. Revisa el nombre completo o indica --usuario-id; no se eligió uno automáticamente.")
    user, employee, full_name = matches[0]
    _load_permission_state(user, fresh=True)
    if not user.is_active or not user_can_access_url_name(user, "generar_venta"):
        raise ActivationCheckError("El usuario está inactivo o no tiene permiso para generar ventas.")
    if not employee or not employee.sucursalid_id:
        raise ActivationCheckError("El usuario no tiene un empleado con sucursal asignada.")
    points = PuntosPago.objects.using("default").filter(sucursalid_id=employee.sucursalid_id)
    matches = [point for point in points if (point.pk == point_id if point_id is not None
                                           else _name(point.nombre) == _name(caja))]
    if len(matches) != 1:
        raise ActivationCheckError("No hay una caja única con ese nombre/ID en la sucursal del usuario. Revisa la selección; no se cambió de sucursal.")
    point = matches[0]
    required = ConfiguracionFuncionalidad.objects.using("default").filter(
        clave=TURN_REQUIRED_FEATURE).values_list("habilitada", flat=True).first()
    required = True if required is None else required
    turn = None
    if required:
        turns = list(TurnoCaja.objects.using("default").filter(cajero_id=user.pk, estado="ABIERTO")[:2])
        if len(turns) != 1 or turns[0].puntopago_id != point.pk:
            raise ActivationCheckError("Para activar ventas offline, este usuario necesita un único turno abierto en la caja elegida. No se abrió ni cerró ningún turno.")
        turn = turns[0]
    if not MetodoPago.objects.using("default").filter(activo=True).exists():
        raise ActivationCheckError("No hay medios de pago activos para esta copia.")
    return {"user_id": user.pk, "user_name": full_name.strip() or user.nombreusuario,
            "point_id": point.pk, "point_name": point.nombre,
            "branch_id": employee.sucursalid_id, "branch_name": employee.sucursalid.nombre,
            "turn_id": turn.pk if turn else None, "required_turn": required}


def inspect_source(usuario=None, caja=None, *, user_id=None, point_id=None):
    """Fresh, bounded READ ONLY transaction; no cached permissions or fallback DB."""
    if (not _name(usuario) and user_id is None) or (not _name(caja) and point_id is None):
        raise ActivationCheckError("Indica explícitamente el usuario y la caja.")
    if any(value is not None and (type(value) is not int or value < 1) for value in (user_id, point_id)):
        raise ActivationCheckError("Los IDs deben ser enteros positivos.")
    from local_pos.request_context import offline_request
    cloud = connections["default"]
    if offline_request() or cloud.vendor != "postgresql" or cloud.in_atomic_block:
        raise ActivationCheckError("La comprobación necesita una conexión PostgreSQL independiente a la nube.")
    original = cloud.settings_dict
    cloud.close()
    cloud.settings_dict = {**original, "OPTIONS": {**original.get("OPTIONS", {}),
        "connect_timeout": 5, "options": "-c statement_timeout=15000 -c lock_timeout=2000"}}
    try:
        with transaction.atomic(using="default"):
            with cloud.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            return _inspect(usuario, caja, user_id=user_id, point_id=point_id)
    except DatabaseError as exc:
        raise ActivationCheckError(database_failure_reason(exc)) from None
    finally:
        cloud.close()
        cloud.settings_dict = original
