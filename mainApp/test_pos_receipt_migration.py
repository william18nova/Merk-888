import importlib

from django.apps import apps
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase, override_settings


class ReceiptMigrationTests(TransactionTestCase):
    def test_0049_creates_receipt_table_with_current_definition(self):
        if connection.vendor == "postgresql":
            self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
            self.assertEqual(connection.settings_dict["NAME"], "test_nova_hybrid_ci")
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(connection)
            state = loader.project_state([("mainApp", "0048_hybrid_business_operations")])
        current = apps.get_model("mainApp", "SolicitudPOS")
        migration = importlib.import_module("mainApp.migrations.0049_solicitudpos").Migration("0049_solicitudpos", "mainApp")
        with connection.schema_editor() as editor:
            editor.delete_model(current)
        with connection.schema_editor() as editor:
            new_state = migration.apply(state, editor)
        historical = new_state.apps.get_model("mainApp", "SolicitudPOS")
        self.assertIn(current._meta.db_table, connection.introspection.table_names())
        for field in current._meta.fields:
            self.assertEqual(field.deconstruct()[1:], historical._meta.get_field(field.name).deconstruct()[1:])
