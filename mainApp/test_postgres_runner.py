"""Build the legacy unmanaged permission table only in the disposable DB."""
from django.conf import settings
from django.test.runner import DiscoverRunner
from django.test import override_settings


class DisposablePostgresRunner(DiscoverRunner):
    def setup_databases(self, **kwargs):
        from mainApp.models import RolPermiso
        database = settings.DATABASES["default"]
        if (settings.SETTINGS_MODULE not in {"NovaSoft.hybrid_test_postgres_settings", "NovaSoft.integrated_test_postgres_settings"}
                or database["HOST"] != "127.0.0.1" or database["NAME"] != "nova_hybrid_ci"
                or database["TEST"]["NAME"] != "test_nova_hybrid_ci"):
            raise RuntimeError("This runner only accepts the isolated disposable test database.")
        if "offline" in settings.DATABASES:
            local = settings.DATABASES["offline"]
            if (local["HOST"] != "127.0.0.1" or local["NAME"] != "nova_integrated_ci"
                    or local["TEST"]["NAME"] != "test_nova_integrated_ci"
                    or local["PORT"] != database["PORT"] or local["USER"] != database["USER"]):
                raise RuntimeError("The offline alias must also be a disposable test database.")
        self.previous_managed = RolPermiso._meta.managed
        RolPermiso._meta.managed = True
        try:
            # Test schemas deliberately contain both apps in both disposable
            # databases so assertions can detect any incorrectly routed write.
            with override_settings(DATABASE_ROUTERS=[]):
                return super().setup_databases(**kwargs)
        except BaseException:
            RolPermiso._meta.managed = self.previous_managed
            raise

    def teardown_databases(self, old_config, **kwargs):
        from mainApp.models import RolPermiso
        try:
            return super().teardown_databases(old_config, **kwargs)
        finally:
            RolPermiso._meta.managed = self.previous_managed
