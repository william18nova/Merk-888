"""Persistent local installation, independent of the cloud settings module."""
from local_pos.runtime import installed_state

_installed = installed_state()
from .hybrid_local_settings import *  # noqa: F403,F401 -- explicitly isolated base.

HYBRID_LOCAL_SALES_ENABLED = True
HYBRID_LOCAL_OPERATIONS_ENABLED = True
LOCAL_SALES_DEMO_CONTROLS = False
LOCAL_INSTALLED_RUNTIME = True

