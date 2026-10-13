"""Bounded offline grants. Only a successful cloud validation renews them."""
from datetime import timedelta
from contextlib import contextmanager

OFFLINE_AUTHORIZATION = timedelta(hours=24)
CLOUD_CONNECT_TIMEOUT = 6


def authorization_expiry(validated_at):
    return validated_at + OFFLINE_AUTHORIZATION


@contextmanager
def bounded_cloud_connection():
    from django.db import connections
    from .request_context import offline_request
    cloud = connections["default"]
    if offline_request() or cloud.in_atomic_block:
        raise RuntimeError("Se necesita una conexión independiente a la nube.")
    original = cloud.settings_dict
    cloud.close()
    cloud.settings_dict = {**original, "OPTIONS": {**original.get("OPTIONS", {}),
        "connect_timeout": CLOUD_CONNECT_TIMEOUT, "options": "-c statement_timeout=15000 -c lock_timeout=3000"}}
    try:
        yield cloud
    finally:
        cloud.close()
        cloud.settings_dict = original
