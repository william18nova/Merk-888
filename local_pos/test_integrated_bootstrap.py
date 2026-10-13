"""No network/database needed: startup boundaries and private configuration."""
from contextlib import nullcontext
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from .integrated_bootstrap import runserver_context
from .integrated_config import (FORMAT, DATABASE, default_path, load_config,
    schema_signature, private_config_path, config_path, register_location)
from .runtime import save_json


class IntegratedBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.project = root / "project"
        for name in ("mainApp/models.py", "mainApp/hybrid_models.py", "local_pos/models.py"):
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"# test fixture\n")
        self.private = root / "private"
        (self.private / "postgres").mkdir(parents=True)
        (self.private / "postgres/PG_VERSION").write_text("16")
        self.path = self.private / "config.json"
        self.config = {"format": FORMAT, "phase": "prepared", "project": str(self.project),
            "data_dir": str(self.private), "instance_id": str(uuid4()), "pg_bin": str(root / "bin"),
            "port": 55460, "database": DATABASE, "user": DATABASE, "password": "p" * 64,
            "secret_key": "k" * 96, "schema_signature": schema_signature(self.project)}
        save_json(self.path, self.config)
        env = dict(os.environ)
        for key in ("DJANGO_SETTINGS_MODULE", "NOVA_INTEGRATED_CONFIG", "RUN_MAIN"):
            env.pop(key, None)
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_valid_config_is_bound_to_exact_project_and_private_schema(self):
        self.assertEqual(load_config(self.path, self.project), self.config)
        self.assertNotEqual(default_path(self.project), default_path(self.project / "other"))

    def test_dotdot_cannot_place_private_config_in_repository(self):
        path = self.private / ".." / "project" / "config.json"
        with self.assertRaisesRegex(RuntimeError, "fuera del repositorio"):
            private_config_path(path, self.project)

    def test_schema_change_requires_upgrade_not_empty_reinitialization(self):
        (self.project / "local_pos/models.py").write_bytes(b"# changed fixture\n")
        with self.assertRaisesRegex(RuntimeError, "esquema"):
            load_config(self.path, self.project)

    def test_incomplete_or_foreign_config_is_not_activated(self):
        for field, value in (("phase", "initializing"), ("database", "postgres"),
                             ("data_dir", str(self.project)), ("port", True), ("password", "short")):
            with self.subTest(field=field):
                save_json(self.path, {**self.config, field: value})
                with self.assertRaises(RuntimeError):
                    load_config(self.path, self.project)

    def test_missing_explicit_config_is_not_ignored(self):
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.private / "missing.json")
        with self.assertRaisesRegex(RuntimeError, "No existe"):
            with runserver_context(["manage.py", "runserver"], self.project):
                self.fail("Invalid configuration was accepted")

    def test_without_preparation_normal_runserver_is_unchanged(self):
        with patch("local_pos.integrated_bootstrap.config_path", return_value=self.private / "missing.json"), \
                patch("local_pos.integrated_bootstrap.Postgres") as postgres:
            with runserver_context(["manage.py", "runserver"], self.project):
                self.assertNotIn("DJANGO_SETTINGS_MODULE", os.environ)
            postgres.assert_not_called()

    def test_migrations_shell_collectstatic_and_explicit_settings_never_start_local_db(self):
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.path)
        with patch("local_pos.integrated_bootstrap.Postgres") as postgres:
            for args in (("migrate",), ("shell",), ("collectstatic",), ("test",),
                         ("runserver", "--settings=NovaSoft.test_settings"),
                         ("runserver", "--settings", "NovaSoft.test_settings")):
                with runserver_context(["manage.py", *args], self.project):
                    self.assertNotIn("DJANGO_SETTINGS_MODULE", os.environ)
            postgres.assert_not_called()

    def test_cloud_wsgi_configuration_is_never_overridden(self):
        os.environ["DJANGO_SETTINGS_MODULE"] = "NovaSoft.other_settings"
        with patch("local_pos.integrated_bootstrap.Postgres") as postgres:
            with runserver_context(["manage.py", "runserver"], self.project):
                self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "NovaSoft.other_settings")
            postgres.assert_not_called()

    def test_runserver_owns_private_database_and_stops_it_on_failure(self):
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.path)
        with patch("local_pos.integrated_bootstrap.Postgres") as postgres, \
                patch("local_pos.integrated_bootstrap.installation_lock", return_value=nullcontext()), \
                patch("django.db.connections.close_all") as close:
            with self.assertRaisesRegex(ValueError, "test"):
                with runserver_context(["manage.py", "runserver", "127.0.0.1:8935", "--noreload"], self.project):
                    self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "NovaSoft.integrated_settings")
                    postgres.return_value.start.assert_called_once()
                    raise ValueError("test")
            postgres.return_value.stop.assert_called_once()
            close.assert_called_once()

    def test_autoreload_child_does_not_start_or_stop_parent_database(self):
        os.environ.update(NOVA_INTEGRATED_CONFIG=str(self.path), RUN_MAIN="true",
                          DJANGO_SETTINGS_MODULE="NovaSoft.integrated_settings")
        with patch("local_pos.integrated_bootstrap.Postgres") as postgres:
            with runserver_context(["manage.py", "runserver"], self.project):
                pass
            postgres.assert_not_called()

    def test_network_listen_addresses_are_not_allowed(self):
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.path)
        with self.assertRaisesRegex(RuntimeError, "localhost"):
            with runserver_context(["manage.py", "runserver", "0.0.0.0:8000"], self.project):
                self.fail("Exposed private runtime to the network")

    def test_failed_start_does_not_stop_an_existing_cluster(self):
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.path)
        with patch("local_pos.integrated_bootstrap.Postgres") as postgres, \
                patch("local_pos.integrated_bootstrap.installation_lock", return_value=nullcontext()):
            postgres.return_value.start.side_effect = RuntimeError("already in use")
            with self.assertRaises(RuntimeError):
                with runserver_context(["manage.py", "runserver"], self.project):
                    pass
            postgres.return_value.stop.assert_not_called()

    def test_registered_physical_location_survives_different_appdata(self):
        register_location(self.path, self.project)
        with patch.dict(os.environ, {"LOCALAPPDATA": str(self.private / "different")}):
            self.assertEqual(config_path(self.project), self.path.resolve())
        self.assertNotIn(self.config["password"], (self.project / ".novapos-runtime.json").read_text())
        register_location(self.path, self.project)  # Idempotent.

    def test_explicit_environment_has_priority_over_locator(self):
        register_location(self.path, self.project)
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.private / "explicit.json")
        self.assertEqual(config_path(self.project), self.private / "explicit.json")

    def test_malformed_or_relative_locator_is_rejected(self):
        for data in ({"format": "unknown", "config": str(self.path)},
                     {"format": "nova-integrated-location-v1", "config": "config.json"}, []):
            with self.subTest(data=data):
                save_json(self.project / ".novapos-runtime.json", data)
                with self.assertRaises(RuntimeError):
                    config_path(self.project)

    def test_locator_cannot_reference_credentials_inside_repository(self):
        save_json(self.project / ".novapos-runtime.json", {
            "format": "nova-integrated-location-v1", "config": str(self.project / "secret.json")})
        with self.assertRaisesRegex(RuntimeError, "fuera del repositorio"):
            config_path(self.project)

    def test_missing_registered_installation_never_falls_back_silently_to_cloud(self):
        register_location(self.path, self.project)
        self.path.unlink()
        with self.assertRaisesRegex(RuntimeError, "No existe"):
            with runserver_context(["manage.py", "runserver"], self.project):
                self.fail("Missing registered configuration was ignored")

    def test_existing_locator_not_rebound_even_with_environment_override(self):
        save_json(self.project / ".novapos-runtime.json", {
            "format": "nova-integrated-location-v1", "config": str(self.private / "other.json")})
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.path)
        with self.assertRaisesRegex(RuntimeError, "otra instalación"):
            register_location(self.path, self.project)
