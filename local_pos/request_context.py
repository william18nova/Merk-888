"""Bind a COMPLETE synchronous Django request to one database and cache.

ConnectionHandler/CacheHandler storage is thread/context local. Settings are
never mutated per request; workers keep the original cloud default connection.
Do not call this to retry a failed SQL statement or inside an atomic block.
"""
from contextlib import contextmanager
from contextvars import ContextVar

from django.core.cache import caches
from django.db import connections

_mode = ContextVar("nova_request_database", default="cloud")
_bound = ContextVar("nova_request_bound", default=False)


def offline_request():
    return _mode.get() == "offline"


@contextmanager
def database_context(alias):
    if alias not in {"default", "offline"}:
        raise ValueError("Unknown POS database context")
    if _bound.get():
        raise RuntimeError("Una solicitud no puede cambiar de base mientras está en curso.")
    original = connections["default"]
    target = connections[alias]
    if original.in_atomic_block or target.in_atomic_block:
        raise RuntimeError("No se puede cambiar la conexión dentro de una transacción.")
    if any(db.connection is not None and not db.autocommit for db in (original, target)):
        raise RuntimeError("No se puede cambiar una conexión con una transacción manual.")
    previous_cache = caches["default"]
    token = _mode.set("offline" if alias == "offline" else "cloud")
    bound_token = _bound.set(True)
    try:
        if alias == "offline":
            connections["default"] = target
            caches["default"] = caches["offline"]
        yield
    finally:
        connections["default"] = original
        caches["default"] = previous_cache
        _mode.reset(token)
        _bound.reset(bound_token)


class IntegratedRouter:
    """Sessions and local journals NEVER depend on the Aiven connection."""
    def db_for_read(self, model, **hints):
        if model._meta.app_label in {"local_pos", "sessions"}:
            return "offline"
        return None

    db_for_write = db_for_read

    def allow_migrate(self, db, app_label, **hints):
        # The local schema has its own isolated one-time initializer. Running
        # ordinary migrations must not create local tables on Aiven.
        if db == "offline" or app_label == "local_pos":
            return False
        return None
