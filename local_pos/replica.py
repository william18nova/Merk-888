"""Descarga durable y activación atómica de datos de referencia autorizados."""
from collections import Counter
from decimal import Decimal
import math
from uuid import UUID, uuid4

from django.conf import settings
from django.core.management.color import no_style
from django.db import connections, transaction, models
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from mainApp.models import (Categoria, Cliente, Inventario, MetodoPago, Producto, Sucursal, PuntosPago,
                            Egreso, PagoVenta, Venta, TurnoCajaMedio, ReintegroVenta)
from pos_shared.replica import VERSION, MAX_ROWS, MAX_BYTES, PAGE_SIZE, ENTITIES, FIELDS, canonical, digest, manifest, row_key
from .models import LocalNode, LocalCommand, ReplicaState, ReplicaRow, ReplicaTransfer, ReplicaPage, LocalSaleSession
from .sales import KIND as SALE_KIND, projected_rows

MODELS = dict(zip(ENTITIES, (Sucursal, PuntosPago, Categoria, Producto, Inventario, Cliente, MetodoPago)))
PAYMENT_REFERENCES = ((Egreso, "medio_pago"), (PagoVenta, "medio_pago"), (Venta, "mediopago"),
                      (TurnoCajaMedio, "metodo"), (ReintegroVenta, "medio_pago"))


class ReplicaError(ValueError):
    pass


class ReplicaScopeError(ReplicaError):
    status = 403
    code = "replica_scope"


def fail(message):
    raise ReplicaError(message)


def validate_descriptor(data, transfer, state):
    if not isinstance(data, dict) or data.get("protocol") != VERSION or data.get("snapshot_id") != str(transfer.pk):
        fail("Versión o identificador de descarga inválido.")
    scope = data.get("scope", {})
    if (not isinstance(scope, dict) or scope.get("device_id") != str(state.device_id)
            or any(type(scope.get(key)) is not int or scope[key] <= 0 for key in ("user_id", "branch_id", "point_id"))):
        fail("La descarga no pertenece a este equipo.")
    if not isinstance(scope.get("routes"), list) or any(not isinstance(name, str) for name in scope["routes"]):
        fail("Permisos de descarga inválidos.")
    if state.scope and state.scope != scope:
        raise ReplicaScopeError("El usuario, la sucursal o los permisos cambiaron; se requiere revisar la vinculación.")
    if data.get("base_id") not in (None, str(transfer.base_id)):
        fail("La descarga se basa en otra copia local.")
    count = data.get("changes_count")
    if type(count) is not int or not 0 <= count <= 2 * MAX_ROWS or data.get("page_size") != PAGE_SIZE:
        fail("Tamaño de descarga inválido.")
    if not isinstance(data.get("manifest"), str) or len(data["manifest"]) != 64:
        fail("Falta la huella de la descarga.")
    expires = parse_datetime(str(data.get("expires_at", "")))
    if not expires or timezone.is_naive(expires) or expires <= timezone.now():
        fail("La autorización de descarga venció; conserva la última copia.")
    return expires


def validate_rows(rows, scope):
    if len(rows) > MAX_ROWS or len(canonical(rows).encode()) > MAX_BYTES:
        fail("La descarga supera los límites locales.")
    groups = {name: {} for name in ENTITIES}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"entity", "id", "values"} or row["entity"] not in MODELS:
            fail("Entidad de descarga no permitida.")
        entity, pk, values = row["entity"], row["id"], row["values"]
        if not isinstance(pk, str) or not pk or len(pk) > 100 or pk in groups[entity]:
            fail("Identificador duplicado o inválido.")
        if entity != "payment_method" and (not pk.isascii() or not pk.isdigit() or not 0 < int(pk) < 2**31 or str(int(pk)) != pk):
            fail("Identificador numérico inválido.")
        if not isinstance(values, dict) or set(values) != set(FIELDS[entity]):
            fail("La descarga contiene campos ajenos al contrato.")
        for key, value in values.items():
            field = MODELS[entity]._meta.get_field(key)
            if value is None:
                if not field.null:
                    fail("Falta un campo obligatorio.")
                continue
            if isinstance(field, models.BooleanField) and type(value) is not bool:
                fail("Valor booleano inválido.")
            if isinstance(field, (models.IntegerField, models.ForeignKey)) and type(value) is not int:
                fail("Cantidad o referencia inválida.")
            if isinstance(field, (models.CharField, models.TextField, models.DecimalField)) and not isinstance(value, str):
                fail("Texto o importe inválido.")
            if isinstance(field, models.FloatField) and (type(value) not in (int, float) or not math.isfinite(value)):
                fail("Porcentaje inválido.")
            if not isinstance(field, models.ForeignKey):
                # No usar full_clean: consultaría relaciones en otra conexión.
                field.run_validators(field.to_python(value))
        groups[entity][pk] = values
    if set(groups["branch"]) != {str(scope["branch_id"])} or set(groups["point"]) != {str(scope["point_id"])}:
        fail("La descarga incluye otra sucursal o caja.")
    if groups["point"][str(scope["point_id"])]["sucursalid_id"] != scope["branch_id"]:
        fail("La caja pertenece a otra sucursal.")
    for product in groups["product"].values():
        if str(product["categoria_id"]) not in groups["category"]:
            fail("Falta una categoría requerida.")
    for stock in groups["stock"].values():
        if stock["sucursalid_id"] != scope["branch_id"] or str(stock["productoid_id"]) not in groups["product"]:
            fail("Inventario fuera de la sucursal autorizada.")
    if not scope.get("clients") and groups["client"]:
        fail("No hay permiso para descargar clientes.")
    return groups


def current_rows(using):
    rows = []
    for entity, model in MODELS.items():
        for record in model.objects.using(using).values("pk", *FIELDS[entity]):
            pk = record.pop("pk")
            rows.append({"entity": entity, "id": str(pk), "values": {
                key: str(value) if isinstance(value, Decimal) else value for key, value in record.items()}})
    return rows


def activate(transfer_id, *, using="default"):
    with transaction.atomic(using=using):
        transfer = ReplicaTransfer.objects.using(using).get(pk=transfer_id)
        LocalNode.objects.using(using).select_for_update().get(pk=transfer.node_id)
        state = ReplicaState.objects.using(using).select_for_update().get(node_id=transfer.node_id)
        transfer = ReplicaTransfer.objects.using(using).select_for_update().get(pk=transfer_id)
        if transfer.applied:
            return state
        if not transfer.downloaded or transfer.abandoned:
            fail("La descarga todavía no está completa.")
        if state.active_id != transfer.base_id:
            fail("Otra actualización ya cambió la copia local.")
        expires = validate_descriptor(transfer.descriptor, transfer, state)
        from .sales import KINDS
        if LocalCommand.objects.using(using).filter(node=state.node, state__in=["intent", "pending", "conflict"]).exclude(kind__in=KINDS).exists():
            fail("Hay operaciones locales pendientes; primero deben conciliarse para no sobrescribirlas.")
        saved = [{"entity": row.entity, "id": row.source_id, "values": row.values}
                 for row in ReplicaRow.objects.using(using).filter(node_id=state.node_id)]
        # No mezclar datos ficticios previos, escrituras manuales o datos de otra
        # instalación. Tampoco sobrescribir cambios locales no conciliados.
        current = current_rows(using)
        if manifest(current) != manifest(projected_rows(saved, state.sale_cursor, node_id=state.node_id, using=using)):
            fail("Hay datos locales fuera de la réplica; no se sobrescribieron.")
        before = {row_key(row): row for row in saved}
        desired = before.copy() if transfer.descriptor["base_id"] else {}
        pages = ReplicaPage.objects.using(using).filter(transfer=transfer).order_by("offset")
        offset, changed = 0, set()
        for page in pages:
            if page.offset != offset:
                fail("Falta una página de la descarga.")
            for change in page.changes:
                if not isinstance(change, dict) or change.get("entity") not in MODELS or not isinstance(change.get("id"), str):
                    fail("Cambio de descarga inválido.")
                key = row_key(change)
                if key in changed:
                    fail("Cambio repetido en la descarga.")
                changed.add(key)
                if change.get("op") == "delete" and set(change) == {"op", "entity", "id"}:
                    if key not in desired:
                        fail("Baja sin registro previo.")
                    del desired[key]
                elif change.get("op") == "upsert" and set(change) == {"op", "entity", "id", "values"}:
                    desired[key] = {key: change[key] for key in ("entity", "id", "values")}
                else:
                    fail("Operación de descarga desconocida.")
            offset += len(page.changes)
        descriptor = transfer.descriptor
        rows = list(desired.values())
        if offset != descriptor["changes_count"] or manifest(rows) != descriptor["manifest"]:
            fail("La descarga está incompleta o cambió su contenido.")
        if dict(Counter(row["entity"] for row in rows)) != descriptor["counts"]:
            fail("Los totales de la descarga no coinciden.")
        validate_rows(rows, descriptor["scope"])
        cursor = descriptor.get("sale_cursor", {})
        if LocalSaleSession.objects.using(using).filter(node_id=state.node_id).exists() and not cursor:
            fail("El servidor no indica qué ventas incluye el inventario; no se reemplazó la copia.")
        if cursor and (cursor.get("session_id") != str(transfer.session_id)
                       or type(cursor.get("sequence")) is not int or cursor["sequence"] < 0):
            fail("El corte de inventario tiene una confirmación inválida.")
        if (cursor and state.sale_cursor.get("session_id") == cursor["session_id"]
                and cursor["sequence"] < state.sale_cursor.get("sequence", 0)):
            fail("La confirmación de ventas retrocedió; se requiere revisar la recuperación del servidor.")
        effective = projected_rows(rows, cursor, node_id=state.node_id, using=using)
        previous_effective = {row_key(row): row for row in current}
        updates = [row for row in effective if previous_effective.get(row_key(row)) != row]
        reference_updates = [row for key, row in desired.items() if before.get(key) != row]
        removals = {entity: [row["id"] for key, row in before.items()
                             if row["entity"] == entity and key not in desired] for entity in ENTITIES}
        for entity in reversed(ENTITIES):
            model = MODELS[entity]
            for obj in model.objects.using(using).filter(pk__in=removals[entity]):
                # Nunca borrar en cascada ventas, turnos, usuarios o historial.
                for relation in model._meta.related_objects:
                    if relation.related_model.objects.using(using).filter(**{relation.field.attname: obj.pk}).exists():
                        fail("Una baja tiene historial vinculado; se requiere conciliación, no borrado automático.")
                # Estos modelos heredados guardan el código como texto, no FK.
                if entity == "payment_method" and any(
                        related.objects.using(using).filter(**{field: obj.pk}).exists()
                        for related, field in PAYMENT_REFERENCES):
                    fail("Una baja tiene historial vinculado; se requiere conciliación, no borrado automático.")
                obj.delete(using=using)
            ReplicaRow.objects.using(using).filter(node_id=state.node_id, entity=entity,
                                                   source_id__in=removals[entity]).delete()
        # Permite cambiar cuál es efectivo sin violar el índice único durante
        # pasos intermedios; todo se confirma o revierte en esta transacción.
        payment_updates = [row["id"] for row in updates if row["entity"] == "payment_method"]
        if payment_updates:
            MetodoPago.objects.using(using).filter(pk__in=payment_updates, es_efectivo=True).update(es_efectivo=False)
        for entity in ENTITIES:
            model = MODELS[entity]
            for row in updates:
                if row["entity"] == entity:
                    model.objects.using(using).update_or_create(pk=row["id"], defaults=row["values"])
        # Una actualización sin cambios solo renueva metadatos/autorización;
        # no reescribe el catálogo ni altera sus fechas automáticamente.
        if reference_updates:
            ReplicaRow.objects.using(using).bulk_create([ReplicaRow(node_id=state.node_id,
                entity=row["entity"], source_id=row["id"], values=row["values"]) for row in reference_updates],
                batch_size=500, update_conflicts=True, update_fields=["values"],
                unique_fields=["node", "entity", "source_id"])
        state.active_id = transfer.pk
        state.scope, state.manifest = descriptor["scope"], descriptor["manifest"]
        state.sale_cursor = cursor
        state.synchronized_at, state.expires_at = timezone.now(), expires
        state.blocked, state.status = False, "Copia de referencia actualizada"
        state.save(using=using)
        transfer.applied = True
        transfer.save(using=using, update_fields=["applied"])
        inserted_entities = {row["entity"] for row in updates if row_key(row) not in before}
        if inserted_entities:
            with connections[using].cursor() as cursor:
                for sql in connections[using].ops.sequence_reset_sql(
                        no_style(), [MODELS[entity] for entity in ENTITIES if entity in inserted_entities]):
                    cursor.execute(sql)
        # Acotar staging local; la copia activa y el historial de comandos quedan.
        ReplicaTransfer.objects.using(using).filter(node_id=state.node_id).exclude(pk=transfer.pk).delete()
        return state


def synchronize(remote, *, local_user_id, using="default"):
    if not getattr(settings, "HYBRID_LOCAL_ENABLED", False):
        fail("La réplica solo se descarga desde el runtime local.")
    node_id = settings.LOCAL_CONFIG["instance_id"]
    with transaction.atomic(using=using):
        node = LocalNode.objects.using(using).select_for_update().get(pk=node_id)
        state, _ = ReplicaState.objects.using(using).get_or_create(node=node, defaults={
            "local_user_id": local_user_id, "server": remote.url, "device_id": UUID(remote.device_id)})
        if state.local_user_id != local_user_id or state.server != remote.url or str(state.device_id) != remote.device_id:
            fail("La instalación ya pertenece a otro usuario, servidor o equipo.")
        transfer = ReplicaTransfer.objects.using(using).filter(node=node, applied=False, abandoned=False).first()
        if transfer and str(transfer.session_id) != remote.session_id:
            transfer.abandoned = True
            transfer.save(using=using, update_fields=["abandoned"])
            transfer = None
        if not transfer:
            transfer = ReplicaTransfer.objects.using(using).create(id=uuid4(), node=node,
                session_id=remote.session_id, base_id=state.active_id)
        state.status = "Descargando; la copia anterior sigue disponible" if state.active_id else "Descargando copia inicial"
        state.save(using=using, update_fields=["status"])
    try:
        description = remote.call("prepare", {"request_id": str(transfer.pk),
                                  "base_id": str(transfer.base_id) if transfer.base_id else None,
                                  "resume": bool(transfer.descriptor)})
        validate_descriptor(description, transfer, state)
        with transaction.atomic(using=using):
            locked = ReplicaTransfer.objects.using(using).select_for_update().get(pk=transfer.pk)
            if locked.descriptor and locked.descriptor != description:
                fail("El servidor cambió una descarga iniciada.")
            locked.descriptor = description
            locked.save(using=using, update_fields=["descriptor"])
        while True:
            transfer.refresh_from_db(using=using)
            if transfer.downloaded:
                break
            offset = transfer.next_offset
            page = remote.call("page", {"snapshot_id": str(transfer.pk), "offset": offset})
            changes = page.get("changes")
            if (page.get("snapshot_id") != str(transfer.pk) or page.get("offset") != offset
                    or not isinstance(changes, list) or len(changes) > PAGE_SIZE
                    or page.get("page_digest") != digest({"offset": offset, "changes": changes})):
                fail("Página incompleta o alterada; no se activó la descarga.")
            expected = min(PAGE_SIZE, description["changes_count"] - offset)
            done = offset + len(changes) == description["changes_count"]
            if len(changes) != expected or page.get("next_offset") != offset + len(changes) or page.get("done") is not done:
                fail("Secuencia de páginas inválida.")
            with transaction.atomic(using=using):
                locked = ReplicaTransfer.objects.using(using).select_for_update().get(pk=transfer.pk)
                if locked.next_offset != offset or locked.downloaded:
                    continue
                ReplicaPage.objects.using(using).create(transfer=locked, offset=offset, changes=changes)
                locked.next_offset, locked.downloaded = offset + len(changes), done
                locked.save(using=using, update_fields=["next_offset", "downloaded"])
        return activate(transfer.pk, using=using)
    except Exception as exc:
        # No guardar textos HTTP, tokens ni payloads externos en mensajes visibles.
        revoked = getattr(exc, "status", None) in (401, 403)
        ReplicaState.objects.using(using).filter(node_id=node_id).update(
            status="Autorización retirada; acceso local bloqueado" if revoked else "Actualización interrumpida; copia anterior conservada",
            **({"blocked": True} if revoked else {}))
        if getattr(exc, "code", None) == "replica_expired":
            ReplicaTransfer.objects.using(using).filter(pk=transfer.pk).update(abandoned=True)
        raise
