"""Two LOCAL disposable databases, never Aiven, with the real integrated overlay."""
from .hybrid_test_postgres_settings import *  # noqa: F401,F403
from local_pos.integrated_configuration import configure

configure(globals(), {"instance_id": "08d71b5d-f55c-452a-ac56-9c75d0cf8bfb"}, {
    **DATABASES["default"], "NAME": "nova_integrated_ci", "TEST": {"NAME": "test_nova_integrated_ci"}})
MIGRATION_MODULES = {**MIGRATION_MODULES, "local_pos": None}
ALLOWED_HOSTS = [*ALLOWED_HOSTS, "testserver"]
