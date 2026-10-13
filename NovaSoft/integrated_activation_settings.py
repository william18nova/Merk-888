"""Operator-only profile: read the cloud, prepare a journal-enabled local copy.

Never selected by normal runserver/WSGI. The activation script commits the
private activation flag only after the backup and snapshot are verified.
"""
from .integrated_settings import *  # noqa: F401,F403

NOVA_INTEGRATED_JOURNAL_ENABLED = True
NOVA_REFERENCE_ENABLED = True
# Defense in depth for any accidental ORM write outside capture's read-only
# transaction. This profile is never used to upload pending sales.
DATABASES = {**DATABASES, "default": {**DATABASES["default"], "OPTIONS": {
    **DATABASES["default"].get("OPTIONS", {}),
    "options": "-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=3000",
    "connect_timeout": 5,
}}}
