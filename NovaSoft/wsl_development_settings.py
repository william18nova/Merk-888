"""Use the WSL local DB without prematurely enabling the unfinished hybrid POS.

Default/cloud, authentication, sessions and business views remain unchanged.
The private PostgreSQL is alias 'offline'; an additive background worker copies
authorized reference data without enabling offline business writes.
The normal runserver bootstrap owns its lifecycle. Never deployed to WSGI.
"""
from .settings import *  # noqa: F403,F401
from local_pos.integrated_config import config_path, load_config, local_database

LOCAL_CONFIG = load_config(config_path(BASE_DIR), BASE_DIR)  # noqa: F405
if LOCAL_CONFIG.get("activation") != "database_only":
    raise RuntimeError("Este perfil solo prepara el motor local de desarrollo.")
DATABASES = {**DATABASES, "offline": local_database(LOCAL_CONFIG)}  # noqa: F405
from local_pos.reference_runtime import configure_reference
configure_reference(globals())
