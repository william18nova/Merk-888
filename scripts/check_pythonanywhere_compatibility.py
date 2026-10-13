"""Check normal WSGI/settings/static assets without connecting to any database.

Run with the production dependencies, on Windows or Linux. Static collection
uses a fresh temporary directory; no existing static files are overwritten.
"""
import io
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.settings"
os.environ["DJANGO_DEBUG"] = "0"


def main():
    from django.db.backends.base.base import BaseDatabaseWrapper
    # A startup check must never touch Aiven, even if local credentials exist.
    with patch.object(BaseDatabaseWrapper, "connect", side_effect=AssertionError("Database access forbidden in startup check")):
        from NovaSoft.wsgi import application
        from django.apps import apps
        from django.conf import settings
        from django.core import checks
        from django.core.management import call_command
        from django.db.migrations.autodetector import MigrationAutodetector
        from django.db.migrations.loader import MigrationLoader
        from django.db.migrations.state import ProjectState
        from django.template.loader import get_template
        from django.test import Client, override_settings
        from local_pos.integrated_bootstrap import runserver_context

        assert application is not None and not settings.DEBUG
        assert set(settings.DATABASES) == {"default"}
        assert not apps.is_installed("local_pos")
        for flag in ("NOVA_INTEGRATED_POS_ENABLED", "NOVA_REFERENCE_ENABLED", "NOVA_INTEGRATED_JOURNAL_ENABLED", "HYBRID_LOCAL_ENABLED"):
            assert not getattr(settings, flag, False), flag
        assert not any("local_pos" in item for item in settings.MIDDLEWARE)
        issues = checks.run_checks()
        assert not [item for item in issues if item.level >= checks.ERROR], [item.id for item in issues]
        print("PASS: normal WSGI, DEBUG=False, no offline app/middleware/database; no SQL connections")

        with patch("local_pos.integrated_bootstrap.load_config", side_effect=AssertionError("Unexpected local startup")):
            for command in ("check", "migrate", "collectstatic", "shell", "test"):
                with runserver_context(["manage.py", command], ROOT):
                    assert os.environ["DJANGO_SETTINGS_MODULE"] == "NovaSoft.settings"
        print("PASS: management commands do not start the local PostgreSQL engine")

        loader = MigrationLoader(None)
        assert not loader.detect_conflicts(), loader.detect_conflicts()
        changes = MigrationAutodetector(loader.project_state(), ProjectState.from_apps(apps)).changes(graph=loader.graph)
        assert ("mainApp", "0050_desglosepagoscierre") in loader.graph.leaf_nodes()
        assert not any(getattr(operation, "model_name", getattr(operation, "name", "")).lower() in {"solicitudpos", "desglosepagoscierre"}
            for migrations in changes.values() for migration in migrations for operation in migration.operations)
        print("PASS: migration graph, migrations 0049/0050 match the new models")
        if changes:
            # This repository predates several manually created production
            # columns/tables. Report drift rather than blindly creating a new
            # migration that could attempt to recreate existing objects.
            operations = [operation.describe() for migrations in changes.values()
                for migration in migrations for operation in migration.operations]
            print("WARNING: other model/migration differences require a schema audit; do not run makemigrations in production:")
            for operation in operations:
                print("  " + operation)

        templates = sorted((ROOT / "mainApp" / "templates").rglob("*.html"))
        for template in templates:
            get_template(template.relative_to(ROOT / "mainApp" / "templates").as_posix())
        print(f"PASS: {len(templates)} Django templates compiled")

        with tempfile.TemporaryDirectory(prefix="nova-cloud-static-check-") as target:
            with override_settings(STATIC_ROOT=target, WHITENOISE_AUTOREFRESH=False, WHITENOISE_USE_FINDERS=False):
                call_command("collectstatic", interactive=False, verbosity=0, stdout=io.StringIO())
                client = Client()
                assets = (
                    "css/navbar.css", "images/logoNovaAdvance.png",
                    "javascript/generar_venta.js", "javascript/turno_caja.js",
                    "javascript/integrated_business_transport.js",
                    "javascript/close_payment_details.js", "css/close_payment_details.css",
                )
                for asset in assets:
                    response = client.get(settings.STATIC_URL + asset)
                    assert response.status_code == 200, (asset, response.status_code)
                    response.close()
                print(f"PASS: collectstatic and HTTP 200 for {len(assets)} representative assets with DEBUG=False")
        print("PASS: startup/static compatibility checks; review warnings above. Production has not been accessed or deployed")


if __name__ == "__main__":
    main()
