"""Fallos de actualización/recuperación con archivos ficticios, sin servidor SQL."""
import json
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from . import runtime as rt
from . import upgrades as up


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = rt.private_dir(self.base / "data")
        self.backup = self.base / "backup-new"
        self.old = self.base / "old-package"
        self.new = self.base / "new-package"
        self.old.mkdir(); self.new.mkdir()
        self.identity = str(uuid4())
        self.state = {"format": rt.FORMAT, "phase": "ready", "instance_id": self.identity,
                      "system": platform.system(), "package_id": "old", "pg_bin": str(self.base)}
        self.config = {"instance_id": self.identity, "data_dir": str(self.root),
                       "database": rt.DB, "user": rt.DB, "password": "test-only", "port": 55441}
        rt.save_json(self.root / "installation.json", self.state)
        rt.save_json(self.root / "local.json", self.config)
        (self.root / "postgres/pg_notify").mkdir(parents=True)
        (self.root / "postgres/PG_VERSION").write_text("18")
        (self.root / "postgres/data").write_bytes(b"original pending operations")
        patches = [patch.object(rt, "ROOT", self.new),
                   patch.object(rt, "package_manifest", side_effect=lambda path=None:
                                {"package_id": "old" if path == self.old else "new"}),
                   patch.object(up, "schema_version", return_value=2),
                   patch.object(rt, "Postgres"),
                   patch.object(up, "_probe", return_value={"rows": 123})]
        self.handles = [p.start() for p in patches]
        for p in patches: self.addCleanup(p.stop)
        self.pg = self.handles[-2].return_value
        self.pg.status.return_value = False
        self.probe = self.handles[-1]

    def upgrade(self):
        return up.upgrade(self.root, previous_package=self.old, backup_dir=self.backup)

    def rollback(self):
        return up.rollback(self.root, backup_dir=self.backup, confirm_instance=self.identity)

    def assert_original(self):
        self.assertEqual(rt.read_json(self.root / "installation.json"), self.state)
        self.assertEqual((self.root / "postgres/data").read_bytes(), b"original pending operations")
        self.assertTrue((self.root / "postgres/pg_notify").is_dir())
        self.assertFalse((self.root / up.MARKER).exists())

    def test_success_and_rollback_keep_identity_and_pending_data(self):
        record = self.upgrade()
        self.assertEqual(record["phase"], "committed")
        self.assertEqual(rt.read_json(self.root / "installation.json")["package_id"], "new")
        self.assertTrue((self.backup / "before.zip").is_file())
        self.rollback()
        self.assert_original()
        self.rollback()  # Repetir no restaura encima de nuevos datos.
        self.assert_original()

    def test_verification_failure_recovers_automatically(self):
        def fail(*args, **kwargs):
            (self.root / "postgres/data").write_bytes(b"failed upgrade")
            raise RuntimeError("simulated migration failure")
        self.probe.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "versión anterior"):
            self.upgrade()
        self.assert_original()
        self.assertEqual((self.backup / "failed-state/postgres/data").read_bytes(), b"failed upgrade")

    def test_interrupted_rollback_can_resume_with_cluster_folder_missing(self):
        self.probe.side_effect = RuntimeError("simulated verification failure")
        real_move = up._move
        def interrupted(source, destination, **kwargs):
            if source == self.backup / "restore-data/postgres":
                raise RuntimeError("simulated interruption")
            return real_move(source, destination, **kwargs)
        with patch.object(up, "_move", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "incompleta"):
                self.upgrade()
        self.assertFalse((self.root / "postgres").exists())
        self.probe.side_effect = None
        self.rollback()
        self.assert_original()

    def test_pending_handoff_blocks_upgrade_before_backup(self):
        rt.save_json(self.root / "handoff-intent.json", {})
        with self.assertRaisesRegex(RuntimeError, "transición"):
            self.upgrade()
        self.assertFalse(self.backup.exists())
        self.assertEqual(rt.read_json(self.root / "installation.json"), self.state)

    def test_busy_postgres_is_not_stopped_by_upgrade(self):
        self.pg.status.return_value = True
        with self.assertRaisesRegex(RuntimeError, "Detén"):
            self.upgrade()
        self.pg.stop.assert_not_called()
        self.assertFalse(self.backup.exists())

    def test_rollback_refuses_new_business_data(self):
        self.upgrade()
        self.probe.return_value = {"rows": 124}
        with self.assertRaisesRegex(RuntimeError, "información nueva"):
            self.rollback()
        self.assertEqual(rt.read_json(self.root / "installation.json")["package_id"], "new")
        self.assertFalse((self.root / up.MARKER).exists())

    def test_rollback_refuses_changed_device_binding(self):
        self.upgrade()
        rt.save_json(self.root / "replica-connection.json", {"fictitious": True})
        with self.assertRaisesRegex(RuntimeError, "cambió después"):
            self.rollback()
        self.assertFalse((self.root / up.MARKER).exists())

    def test_rollback_refuses_corrupt_backup(self):
        self.upgrade()
        with (self.backup / "before.zip").open("ab") as stream: stream.write(b"changed")
        with self.assertRaisesRegex(RuntimeError, "respaldo cambió"):
            self.rollback()
        self.assertEqual(rt.read_json(self.root / "installation.json")["package_id"], "new")

    def test_rollback_uses_verified_state_from_archive(self):
        self.upgrade()
        rt.save_json(self.backup / "original-state.json", {"instance_id": "accidental edit"})
        self.rollback()
        self.assert_original()

    def test_unknown_schema_does_not_touch_data(self):
        with patch.object(up, "schema_version", side_effect=RuntimeError("Esquema desconocido")):
            with self.assertRaisesRegex(RuntimeError, "desconocido"):
                self.upgrade()
        self.assert_original()
        self.assertFalse(self.backup.exists())

    def test_wrong_identity_and_backup_inside_data_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "carpeta NUEVA"):
            up.upgrade(self.root, previous_package=self.old, backup_dir=self.root / "backup")
        self.upgrade()
        with self.assertRaisesRegex(RuntimeError, "identidad"):
            up.rollback(self.root, backup_dir=self.backup, confirm_instance=str(uuid4()))
