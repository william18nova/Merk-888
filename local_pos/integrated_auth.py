"""Keep the authenticated cloud identity usable in a bounded offline snapshot."""
from datetime import timedelta
import hashlib
import json

from django.conf import settings
from django.contrib.auth.backends import ModelBackend
from django.db import transaction
from django.utils import timezone

from .models import IntegratedIdentity, LocalNode
from .request_context import offline_request
from .integrated_policy import authorization_expiry


def cloud_fingerprint():
    database = settings.DATABASES["default"]
    identity = {key: str(database.get(key, "")) for key in ("ENGINE", "HOST", "PORT", "NAME", "USER")}
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def authorized_identity(user):
    if not user or not user.is_active:
        return None
    return IntegratedIdentity.objects.using("offline").filter(user_id=user.pk,
        node_id=settings.LOCAL_CONFIG["instance_id"], cloud_fingerprint=cloud_fingerprint(),
        expires_at__gt=timezone.now()).first()


class IntegratedBackend(ModelBackend):
    def get_user(self, user_id):
        user = super().get_user(user_id)
        if offline_request() and not authorized_identity(user):
            return None
        return user

    def authenticate(self, request, username=None, password=None, **kwargs):
        user = super().authenticate(request, username, password, **kwargs)
        if offline_request() and not authorized_identity(user):
            return None
        return user


def _copy(instance):
    """Bulk upsert deliberately avoids business save hooks (employee -> client)."""
    model = type(instance)
    fields = [field for field in model._meta.concrete_fields]
    values = {field.attname: getattr(instance, field.attname) for field in fields}
    model.objects.using("offline").bulk_create([model(**values)], update_conflicts=True,
        unique_fields=[model._meta.pk.name], update_fields=[field.name for field in fields if not field.primary_key])


def collect_identity(user_id):
    """Read an identity inside the caller's consistent cloud snapshot."""
    from mainApp.models import Usuario, RolPermiso, UsuarioPermiso, Permiso
    from mainApp.permissions import _load_permission_state, user_can_access_url_name
    from mainApp.services.hybrid_replica import REPLICA_READ_ROUTES
    actor = Usuario.objects.select_related("rolid", "empleado__sucursalid").get(pk=user_id)
    _load_permission_state(actor, fresh=True)
    role_links = list(RolPermiso.objects.filter(rol_id=actor.rolid_id)) if actor.rolid_id else []
    direct_links = list(UsuarioPermiso.objects.filter(usuario_id=actor.pk))
    permissions = list(Permiso.objects.filter(pk__in={row.permiso_id for row in role_links + direct_links}))
    employee = getattr(actor, "empleado", None)
    routes = [name for name in REPLICA_READ_ROUTES if user_can_access_url_name(actor, name)] if actor.is_active else []
    from django.conf import settings
    if getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False) and actor.is_active and user_can_access_url_name(actor, "generar_venta"):
        routes += ["generar_venta", "verificar_producto", "buscar_producto_por_codigo"]
        from .integrated_business_snapshot import ROUTES
        routes += [name for name in ROUTES if user_can_access_url_name(actor, name) and name not in routes]
    return {"actor": actor, "employee": employee, "permissions": permissions,
            "role_links": role_links, "direct_links": direct_links, "routes": routes}


def apply_identity(snapshot, *, copy_branch=True, now=None):
    from mainApp.models import RolPermiso, UsuarioPermiso
    from .models import ReplicaState
    actor, employee = snapshot["actor"], snapshot["employee"]
    now = now or timezone.now()
    with transaction.atomic(using="offline"):
        node = LocalNode.objects.using("offline").select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
        state = ReplicaState.objects.using("offline").filter(node=node, active_id__isnull=False).first()
        if state and state.scope.get("branch_id") and employee and employee.sucursalid_id != state.scope["branch_id"]:
            IntegratedIdentity.objects.using("offline").filter(user_id=actor.pk).update(expires_at=now)
            return
        if actor.rolid_id:
            _copy(actor.rolid)
        # Once a reference copy exists its branch belongs to that atomic copy;
        # identity refresh must not independently rewrite reference rows.
        if copy_branch and not state and employee and employee.sucursalid_id:
            _copy(employee.sucursalid)
        _copy(actor)
        if employee:
            _copy(employee)
        for permission in snapshot["permissions"]:
            _copy(permission)
        if actor.rolid_id:
            RolPermiso.objects.using("offline").filter(rol_id=actor.rolid_id).delete()
            RolPermiso.objects.using("offline").bulk_create([RolPermiso(rol_id=row.rol_id, permiso_id=row.permiso_id) for row in snapshot["role_links"]])
        UsuarioPermiso.objects.using("offline").filter(usuario_id=actor.pk).delete()
        UsuarioPermiso.objects.using("offline").bulk_create([UsuarioPermiso(usuario_id=row.usuario_id,
            permiso_id=row.permiso_id, permitido=row.permitido) for row in snapshot["direct_links"]])
        IntegratedIdentity.objects.using("offline").update_or_create(user_id=actor.pk, defaults={
            "node": node, "cloud_fingerprint": cloud_fingerprint(), "refreshed_at": now,
            "expires_at": authorization_expiry(now) if actor.is_active else now,
            "allowed_routes": snapshot["routes"]})
    from django.core.cache import caches
    caches["offline"].clear()


def remember_user(user, *, force=False):
    if offline_request() or not user.is_authenticated:
        return
    saved = authorized_identity(user)
    if not force and saved and timezone.now() - saved.refreshed_at < timedelta(seconds=30):
        return
    apply_identity(collect_identity(user.pk))
