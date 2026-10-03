"""Transacción común para módulos futuros; NO reproduce SQL ni peticiones HTTP.

No hay acciones productivas registradas todavía. Cada adaptador deberá validar
permisos y datos del dominio, y proporcionar la misma operación en la nube.
"""
import hashlib
import json
from uuid import UUID
from django.conf import settings
from django.db import transaction
from .models import LocalNode, LocalCommand


class LocalCommandError(ValueError):
    pass


class CommandRegistry:
    def __init__(self):
        self.handlers = {}

    def register(self, kind, *, permission, validate, apply, version=1):
        if kind in self.handlers:
            raise LocalCommandError("La operación ya tiene un adaptador.")
        if not all(callable(fn) for fn in (permission, validate, apply)):
            raise LocalCommandError("Faltan validaciones o permiso del módulo.")
        self.handlers[kind] = (permission, validate, apply, version)

    def execute(self, *, actor, operation_id, kind, payload):
        if not getattr(settings, "HYBRID_LOCAL_ENABLED", False):
            raise LocalCommandError("Este registro pertenece solo al runtime local.")
        if not getattr(actor, "is_authenticated", False) or not actor.is_active:
            raise LocalCommandError("Se requiere un usuario local autorizado.")
        if kind not in self.handlers:
            raise LocalCommandError("La sincronización de esta operación todavía no está implementada.")
        permission, validate, apply, version = self.handlers[kind]
        if not permission(actor):
            raise LocalCommandError("No tienes permiso para esta operación.")
        operation_id = UUID(str(operation_id))
        if not isinstance(payload, dict):
            raise LocalCommandError("Datos inválidos.")
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(body.encode()) > 100000:
            raise LocalCommandError("Operación demasiado grande.")
        digest = hashlib.sha256(f"{actor.pk}:{kind}:{version}:{body}".encode()).hexdigest()
        with transaction.atomic():
            node = LocalNode.objects.select_for_update().get(pk=settings.LOCAL_CONFIG["instance_id"])
            old = LocalCommand.objects.filter(operation_id=operation_id).first()
            if old:
                if old.fingerprint != digest or old.node_id != node.pk:
                    raise LocalCommandError("Esta referencia pertenece a otra operación; no se modificó.")
                return old.local_result
            cleaned = validate(actor, json.loads(body))
            result = apply(actor, cleaned)
            node.sequence += 1
            node.save(update_fields=["sequence"])
            # Si el diario falla, también revierte la modificación del módulo.
            LocalCommand.objects.create(operation_id=operation_id, node=node, sequence=node.sequence,
                actor=actor, kind=kind, version=version, payload=cleaned, fingerprint=digest, local_result=result)
            return result


registry = CommandRegistry()
