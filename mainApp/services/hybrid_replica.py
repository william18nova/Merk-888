"""Descargas inmutables y deltas autorizados. No modifica datos del negocio."""
from collections import Counter
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from mainApp.models import (Categoria, Cliente, Inventario, MetodoPago, Producto,
                            ReplicaHibrida, Sucursal, PuntosPago, Usuario)
from mainApp.permissions import _load_permission_state, user_can_access_url_name
from pos_shared.replica import VERSION, MAX_ROWS, MAX_BYTES, PAGE_SIZE, FIELDS, canonical, digest, manifest, row_key
from . import hybrid

REPLICA_READ_ROUTES = (
    "visualizar_sucursales", "visualizar_categorias", "visualizar_productos", "productos_datatable",
    "visualizar_inventarios", "visualizar_clientes", "producto_snapshot", "producto_autocomplete",
    "producto_autocomplete_id", "producto_autocomplete_codigo", "producto_autocomplete_barras",
    "producto_autocomplete_global", "cliente_autocomplete", "sucursal_autocomplete", "puntopago_autocomplete",
    "categoria_autocomplete", "producto_inventario_autocomplete", "sucursal_con_inventario_autocomplete",
)


def authorize(original, session_id):
    device = hybrid.locked_device(original)
    hybrid.require_device(device)
    session = hybrid.load_session(device, session_id)
    user = Usuario.objects.select_related("rolid").get(pk=session.usuario_id)
    # No prolongar permisos revocados por una caché de varios minutos.
    _load_permission_state(user, fresh=True)
    hybrid.require_user(user, device)
    return device, session, user_scope(device, user)


def user_scope(device, user):
    """Mismo alcance para iniciar sesión, relevar cajeros y descargar la copia."""
    clients = user_can_access_url_name(user, "cliente_autocomplete")
    return {"device_id": str(device.pk), "user_id": user.pk,
             "branch_id": device.punto.sucursalid_id, "point_id": device.punto_id,
             "clients": clients, "routes": [name for name in REPLICA_READ_ROUTES
                                                if user_can_access_url_name(user, name)]}


def collect(scope):
    products = Producto.objects.all().order_by("pk")
    queries = (
        ("branch", Sucursal.objects.filter(pk=scope["branch_id"])),
        ("point", PuntosPago.objects.filter(pk=scope["point_id"])),
        ("category", Categoria.objects.all()),
        ("product", products),
        ("stock", Inventario.objects.filter(sucursalid_id=scope["branch_id"])),
        ("client", Cliente.objects.all() if scope["clients"] else Cliente.objects.none()),
        ("payment_method", MetodoPago.objects.all()),
    )
    rows = []
    for entity, query in queries:
        for record in query.order_by("pk").values("pk", *FIELDS[entity]).iterator(chunk_size=1000):
            pk = record.pop("pk")
            values = {key: str(value) if isinstance(value, Decimal) else value for key, value in record.items()}
            # La copia de referencia no incluye blobs ni descripciones ilimitadas.
            if values.get("descripcion"):
                values["descripcion"] = values["descripcion"][:2000]
            if entity == "product" and "visualizar_productos" not in scope["routes"]:
                values["rentabilidad"] = "0.00"
            rows.append({"entity": entity, "id": str(pk), "values": values})
            if len(rows) > MAX_ROWS:
                raise hybrid.HybridError("La réplica supera el límite de esta etapa.", "replica_limit", 409)
    if len(canonical(rows).encode()) > MAX_BYTES:
        raise hybrid.HybridError("La réplica supera el tamaño de esta etapa.", "replica_limit", 409)
    return sorted(rows, key=row_key)


def describe(snapshot):
    return {"protocol": VERSION, "snapshot_id": str(snapshot.pk),
            "base_id": str(snapshot.base_id) if snapshot.base_id else None,
            "scope": snapshot.alcance, "manifest": snapshot.huella,
            "sale_cursor": snapshot.cursor_ventas,
            "counts": snapshot.resumen, "changes_count": len(snapshot.cambios),
            "page_size": PAGE_SIZE, "created_at": snapshot.creada_en.isoformat(),
            "expires_at": snapshot.vence_en.isoformat()}


def prepare(original, data):
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise hybrid.HybridError("La réplica exige una transacción PostgreSQL independiente.", "replica_configuration", 503)
    with transaction.atomic():
        # Todos los modelos del corte corresponden al mismo estado MVCC. Una
        # actualización durante la descarga aparecerá en el siguiente corte.
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        device, session, scope = authorize(original, data.get("session_id"))
        request_id = hybrid.identifier(data.get("request_id"))
        base_id = hybrid.identifier(data["base_id"]) if data.get("base_id") else None
        request_hash = digest({"device": str(device.pk), "session": str(session.pk), "base": str(base_id)})
        old = ReplicaHibrida.objects.filter(pk=request_id).first()
        if old:
            if old.equipo_id != device.pk or old.solicitud_hash != request_hash or old.alcance != scope:
                raise hybrid.HybridError("La solicitud pertenece a otro alcance.", "replica_scope", 403)
            if old.vence_en <= timezone.now():
                raise hybrid.HybridError("La descarga venció; inicia otra sin borrar tu copia local.", "replica_expired", 409)
            return describe(old)
        if data.get("resume"):
            raise hybrid.HybridError("La descarga ya no está retenida; inicia otra.", "replica_expired", 409)
        latest = ReplicaHibrida.objects.filter(equipo=device).order_by("-creada_en").first()
        interval = getattr(settings, "HYBRID_REPLICA_MIN_INTERVAL", 30)
        if latest and (timezone.now() - latest.creada_en).total_seconds() < interval:
            raise hybrid.HybridError("Espera antes de volver a actualizar la réplica.", "rate_limit", 429)
        base = ReplicaHibrida.objects.filter(pk=base_id, equipo=device, alcance=scope).first() if base_id else None
        rows = collect(scope)
        before = {row_key(row): row for row in base.filas} if base else {}
        after = {row_key(row): row for row in rows}
        changes = [{"op": "upsert", **row} for key, row in after.items() if before.get(key) != row]
        changes += [{"op": "delete", "entity": row["entity"], "id": row["id"]}
                    for key, row in before.items() if key not in after]
        result = ReplicaHibrida.objects.create(id=request_id, equipo=device, sesion=session,
            base_id=base.pk if base else None, solicitud_hash=request_hash, alcance=scope,
            filas=rows, cambios=changes, resumen=dict(Counter(row["entity"] for row in rows)),
            cursor_ventas={"session_id": str(session.pk), "sequence": session.secuencia},
            huella=manifest(rows), vence_en=min(session.vence_en, timezone.now() + timedelta(hours=2)))
        # Retención acotada. Una base retirada provoca una descarga completa,
        # nunca un delta calculado contra una versión distinta.
        keep = list(ReplicaHibrida.objects.filter(equipo=device).order_by("-creada_en").values_list("pk", flat=True)[:4])
        ReplicaHibrida.objects.filter(equipo=device).exclude(pk__in=keep).delete()
        return describe(result)


@transaction.atomic
def page(original, data):
    device, session, scope = authorize(original, data.get("session_id"))
    snapshot = ReplicaHibrida.objects.filter(pk=hybrid.identifier(data.get("snapshot_id")), equipo=device).first()
    if not snapshot:
        raise hybrid.HybridError("La descarga ya no está retenida; inicia otra.", "replica_expired", 409)
    if snapshot.sesion_id != session.pk or snapshot.alcance != scope:
        raise hybrid.HybridError("No tienes acceso a esta descarga.", "replica_scope", 403)
    if snapshot.vence_en <= timezone.now():
        raise hybrid.HybridError("La descarga venció; conserva la copia anterior.", "replica_expired", 409)
    offset = data.get("offset", 0)
    count = len(snapshot.cambios)
    if type(offset) is not int or offset < 0 or offset % PAGE_SIZE or (offset >= count and offset != 0):
        raise hybrid.HybridError("Página de réplica inválida.", "replica_page", 400)
    changes = snapshot.cambios[offset:offset + PAGE_SIZE]
    return {"snapshot_id": str(snapshot.pk), "offset": offset, "changes": changes,
            "page_digest": digest({"offset": offset, "changes": changes}),
            "next_offset": offset + len(changes), "done": offset + len(changes) >= count}
