"""Renovación y relevo explícitos sin descartar historial ni reutilizar autores."""
from uuid import UUID
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from pos_shared.protocol import ProtocolError
from .models import LocalNode, LocalSaleSession, LocalCommand, ReplicaState, LocalOperator, LocalHandoff, ReplicaTransfer


def adopt_session(data, *, previous_id, requested_id, using="default"):
    with transaction.atomic(using=using):
        node = LocalNode.objects.using(using).select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        session = LocalSaleSession.objects.using(using).get(node=node)
        if LocalCommand.objects.using(using).filter(node=node).exclude(state="accepted").exists():
            raise ProtocolError("No se puede cambiar de turno con operaciones pendientes.")
        if (str(session.session_id) not in {previous_id, requested_id} or data.get("session_id") != requested_id
                or type(data.get("sequence")) is not int or data["sequence"] != 0
                or any(session.data.get(key) != data.get(key) for key in ("user_id", "user", "branch_id", "point_id"))):
            raise ProtocolError("La autorización nueva no corresponde al usuario, equipo o sucursal anterior.")
        if str(session.session_id) == previous_id and not session.data.get("closed"):
            raise ProtocolError("Primero confirma el cierre anterior.")
        session.session_id = UUID(requested_id)
        session.data, session.products = data, {}
        session.sequence, session.last_clock, session.online = 0, None, False
        session.save(using=using)
        ReplicaState.objects.using(using).filter(node=node).update(blocked=True, status="Actualizando autorización para el nuevo turno")


def handoff_session(data, *, previous_id, requested_id, username, local_password, using="default"):
    """Llamar solo con la respuesta HTTPS autenticada y el runtime detenido."""
    from mainApp.models import Usuario
    previous_id, requested_id = str(UUID(previous_id)), str(UUID(requested_id))
    if previous_id == requested_id or not isinstance(local_password, str) or len(local_password) < 12:
        raise ProtocolError("Usa una nueva sesión y una contraseña local de al menos 12 caracteres.")
    expires = parse_datetime(str(data.get("expires_at", "")))
    scope = data.get("reference_scope")
    if (not expires or timezone.is_naive(expires) or expires <= timezone.now()
            or data.get("session_id") != requested_id or data.get("user") != username
            or type(data.get("user_id")) is not int or data["user_id"] <= 0
            or type(data.get("sequence")) is not int or data["sequence"] != 0
            or not isinstance(scope, dict) or not isinstance(scope.get("routes"), list)
            or type(scope.get("clients")) is not bool
            or any(scope.get(key) != data.get(key) for key in ("user_id", "branch_id", "point_id"))):
        raise ProtocolError("El servidor no confirmó una autorización nueva y válida para ese cajero.")
    with transaction.atomic(using=using):
        node = LocalNode.objects.using(using).select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        session = LocalSaleSession.objects.using(using).get(node=node)
        state = ReplicaState.objects.using(using).select_for_update().get(node=node)
        if LocalCommand.objects.using(using).filter(node=node).exclude(state="accepted").exists():
            raise ProtocolError("No se puede relevar al cajero con operaciones pendientes o en revisión.")
        if (scope.get("device_id") != str(state.device_id)
                or any(state.scope.get(key) != data.get(key) for key in ("branch_id", "point_id"))):
            raise ProtocolError("El relevo no permite cambiar de equipo, sucursal o punto de pago.")
        old = LocalHandoff.objects.using(using).filter(pk=requested_id).first()
        if old:
            if (str(session.session_id) != requested_id or str(old.previous_session_id) != previous_id
                    or old.next_cloud_user_id != data["user_id"] or state.local_user_id != old.next_actor_id):
                raise ProtocolError("La referencia de relevo pertenece a otra transición.")
            return old.next_actor
        if str(session.session_id) != previous_id or not session.data.get("closed"):
            raise ProtocolError("Primero confirma y concilia el cierre del cajero anterior.")
        if not data.get("turn_id") or data["turn_id"] == session.data.get("turn_id"):
            raise ProtocolError("El siguiente cajero necesita un turno nuevo abierto en la nube.")
        previous_actor = Usuario.objects.using(using).get(pk=state.local_user_id)
        binding, _ = LocalOperator.objects.using(using).get_or_create(node=node, cloud_user_id=session.data["user_id"],
            defaults={"cloud_username": session.data["user"], "user": previous_actor})
        if binding.user_id != previous_actor.pk:
            raise ProtocolError("La identidad local anterior no coincide con su registro remoto.")
        following = LocalOperator.objects.using(using).filter(node=node, cloud_user_id=data["user_id"]).first()
        if following:
            actor = following.user
        else:
            local_name = f"cajero-{data['user_id']}"
            if Usuario.objects.using(using).filter(nombreusuario=local_name).exists():
                raise ProtocolError("El nombre local ya está ocupado; no se reemplazó esa cuenta.")
            actor = Usuario.objects.db_manager(using).create_user(local_name, None, is_active=False)
            following = LocalOperator.objects.using(using).create(node=node, cloud_user_id=data["user_id"],
                cloud_username=username, user=actor)
        following.cloud_username = username
        following.save(using=using, update_fields=["cloud_username"])
        previous_actor.is_active = False
        previous_actor.save(using=using, update_fields=["is_active"])
        # Sin rol heredado, privilegios de administrador ni acceso de otro cajero.
        actor.rolid = None
        actor.is_staff = actor.is_superuser = False
        actor.is_active = True
        actor.set_password(local_password)
        actor.save(using=using, update_fields=["rolid", "is_staff", "is_superuser", "is_active", "password"])
        session.session_id, session.data = UUID(requested_id), data
        session.products, session.sequence, session.last_clock, session.online, session.error = {}, 0, None, False, ""
        session.save(using=using)
        state.local_user = actor
        state.scope, state.blocked, state.expires_at = scope, True, None
        state.status = "Relevo autorizado; falta descargar y verificar la copia del nuevo cajero"
        state.save(using=using)
        ReplicaTransfer.objects.using(using).filter(node=node, applied=False).update(abandoned=True)
        LocalHandoff.objects.using(using).create(session_id=requested_id, node=node, previous_session_id=previous_id,
            previous_actor=previous_actor, next_actor=actor, previous_cloud_user_id=binding.cloud_user_id,
            next_cloud_user_id=following.cloud_user_id)
        synchronize_operator_permissions(session, state, using=using)
        return actor


def synchronize_operator_permissions(session, state, *, using="default"):
    """Grants mínimos derivados de la sesión y copia autorizadas, nunca de su rol anterior."""
    from mainApp.models import Permiso, UsuarioPermiso
    from mainApp.permissions import route_permissions_for_url_name, clear_permission_cache
    operator = LocalOperator.objects.using(using).filter(node_id=state.node_id, user_id=state.local_user_id).first()
    if not operator:
        return  # Compatibilidad con el laboratorio/usuario inicial de esta etapa.
    if operator.cloud_user_id != session.data.get("user_id") or state.scope.get("user_id") != operator.cloud_user_id:
        raise ProtocolError("Los permisos no corresponden al cajero autorizado.")
    routes = [*state.scope.get("routes", []), "generar_venta"]
    if session.data.get("operation_permissions", {}).get("turn.close"):
        routes.append("turno_caja_cerrar")
    codes = {code for route in routes for code in route_permissions_for_url_name(route)}
    if session.data.get("operation_permissions", {}).get("return"):
        codes.add("ventas_cambios")
    UsuarioPermiso.objects.using(using).filter(usuario_id=operator.user_id).delete()
    for code in sorted(codes):
        permission, _ = Permiso.objects.using(using).get_or_create(nombre=code)
        UsuarioPermiso.objects.using(using).create(usuario_id=operator.user_id, permiso=permission, permitido=True)
    clear_permission_cache(operator.user)
