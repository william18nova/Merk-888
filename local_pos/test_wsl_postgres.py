"""Offline unit tests for WSL boundaries; no installed WSL required."""
from contextlib import nullcontext
import os
from pathlib import Path
from unittest.mock import patch

from .test_integrated_bootstrap import IntegratedBootstrapTests
from .integrated_bootstrap import runserver_context
from .integrated_config import load_config
from .runtime import save_json
from .wsl_postgres import validate_backend


class WslBootstrapTests(IntegratedBootstrapTests):
    # Inherited tests keep asserting that native configurations work unchanged.
    def wsl_config(self):
        config = {**self.config, "pg_bin": "/usr/lib/postgresql/16/bin", "backend": {
            "kind": "wsl", "distribution": "Ubuntu-22.04", "user": "novapos-test",
            "data_dir": "/home/novapos-test/.local/share/NovaPOS/integrated/" + self.config["instance_id"]}}
        return config

    def test_linux_config_does_not_need_native_pg_version_or_dll(self):
        config = self.wsl_config()
        (self.private / "postgres/PG_VERSION").unlink()
        save_json(self.path, config)
        self.assertEqual(load_config(self.path, self.project), config)

    def test_refuses_root_foreign_paths_and_shell_injection(self):
        for key, value in (("user", "root"), ("user", "test; rm -rf /"),
            ("distribution", "--exec"), ("data_dir", "/var/lib/postgresql/16/main"),
            ("data_dir", "/home/novapos-test/.local/share/NovaPOS/integrated/../other")):
            with self.subTest(key=key, value=value):
                config = self.wsl_config()
                config["backend"][key] = value
                with self.assertRaises(RuntimeError):
                    validate_backend(config)
        config = self.wsl_config()
        config["pg_bin"] = "C:/Program Files/PostgreSQL/18/bin"
        with self.assertRaises(RuntimeError):
            validate_backend(config)

    def test_wsl_runserver_never_starts_native_postgres(self):
        save_json(self.path, self.wsl_config())
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.path)
        with patch("local_pos.wsl_postgres.WslPostgres") as wsl, \
                patch("local_pos.integrated_bootstrap.Postgres") as native, \
                patch("local_pos.integrated_bootstrap.installation_lock", return_value=nullcontext()), \
                patch("django.db.connections.close_all"):
            with runserver_context(["manage.py", "runserver"], self.project):
                wsl.return_value.start.assert_called_once()
            wsl.return_value.stop.assert_called_once()
            native.assert_not_called()

    def test_wsl_credentials_not_sent_to_linux_supervisor(self):
        from .wsl_postgres import WslPostgres
        instance = object.__new__(WslPostgres)
        instance.config = self.wsl_config()
        data = instance.payload()
        self.assertNotIn("password", data)
        self.assertNotIn("secret_key", data)
        self.assertNotIn("user", data)

    def test_database_only_mode_does_not_activate_unfinished_hybrid(self):
        config = self.wsl_config()
        config["activation"] = "database_only"
        save_json(self.path, config)
        os.environ["NOVA_INTEGRATED_CONFIG"] = str(self.path)
        with patch("local_pos.wsl_postgres.WslPostgres"), \
                patch("local_pos.integrated_bootstrap.installation_lock", return_value=nullcontext()), \
                patch("django.db.connections.close_all"):
            with runserver_context(["manage.py", "runserver"], self.project):
                self.assertEqual(os.environ["DJANGO_SETTINGS_MODULE"], "NovaSoft.wsl_development_settings")

    def test_unknown_activation_is_rejected(self):
        config = self.wsl_config()
        config["activation"] = "unsafe"
        save_json(self.path, config)
        with self.assertRaisesRegex(RuntimeError, "activación"):
            load_config(self.path, self.project)

    def test_waits_for_old_wsl_forwarder_without_killing_port_owner(self):
        from .wsl_postgres import wait_for_forwarding_release
        with patch("local_pos.wsl_postgres.port_free", side_effect=[RuntimeError("busy"), None]) as check, \
                patch("local_pos.wsl_postgres.time.sleep"):
            wait_for_forwarding_release(55460)
            self.assertEqual(check.call_count, 2)

    def test_real_port_conflict_still_fails_closed(self):
        from .wsl_postgres import wait_for_forwarding_release
        with patch("local_pos.wsl_postgres.port_free", side_effect=RuntimeError("busy")):
            with self.assertRaisesRegex(RuntimeError, "busy"):
                wait_for_forwarding_release(55460, timeout=0)
