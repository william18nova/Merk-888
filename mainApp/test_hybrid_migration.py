"""Ejecuta exclusivamente 0044–0047 en la base desechable de pruebas."""
import importlib
from django.apps import apps
from django.conf import settings
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase, override_settings


class HybridMigrationTests(TransactionTestCase):
    def test_hybrid_migrations_create_tables_and_match_current_models(self):
        if connection.vendor == "postgresql":
            self.assertEqual(settings.SETTINGS_MODULE, "NovaSoft.hybrid_test_postgres_settings")
            self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
            self.assertEqual(connection.settings_dict["NAME"], "test_nova_hybrid_ci")
        else:
            self.assertEqual(connection.vendor, "sqlite", "Solo se admiten bases aisladas de prueba.")
        with override_settings(MIGRATION_MODULES={}):
            loader = MigrationLoader(connection)
            state = loader.project_state([("mainApp", "0043_expense_four_per_thousand")])
        migration = importlib.import_module("mainApp.migrations.0044_hybrid_pilot").Migration("0044_hybrid_pilot", "mainApp")
        recovery = importlib.import_module("mainApp.migrations.0045_hybrid_recovery").Migration("0045_hybrid_recovery", "mainApp")
        replica = importlib.import_module("mainApp.migrations.0046_hybrid_reference_replica").Migration("0046_hybrid_reference_replica", "mainApp")
        cursor = importlib.import_module("mainApp.migrations.0047_hybrid_replica_sale_cursor").Migration("0047_hybrid_replica_sale_cursor", "mainApp")
        names = ("EquipoHibrido", "SesionHibrida", "OperacionHibrida", "RecuperacionHibrida", "ReplicaHibrida")
        with connection.schema_editor() as editor:
            for name in reversed(names):
                editor.delete_model(apps.get_model("mainApp", name))
        with connection.schema_editor() as editor:
            new_state = migration.apply(state, editor)
            new_state = recovery.apply(new_state, editor)
            new_state = replica.apply(new_state, editor)
            new_state = cursor.apply(new_state, editor)
        for name in names:
            current = apps.get_model("mainApp",name)
            historical = new_state.apps.get_model("mainApp",name)
            self.assertIn(current._meta.db_table,connection.introspection.table_names())
            self.assertCountEqual([f.name for f in current._meta.fields], [f.name for f in historical._meta.fields])
            self.assertEqual(current._meta.constraints,historical._meta.constraints)
            for field in current._meta.fields:
                actual = historical._meta.get_field(field.name)
                self.assertEqual(field.deconstruct()[1:],actual.deconstruct()[1:])
