"""Offline tests for the backup gate. No production connections or migrations."""
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from local_pos.runtime import save_json
from scripts.migrate_hybrid_with_backup import EXPECTED, checked_plan, verified_backup


class VerifiedBackupTests(SimpleTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "manifest.json"
        self.archive = self.root / "database.dump"
        self.archive.write_bytes(b"fictional backup bytes")
        self.database = {"HOST": "fixture.invalid", "PORT": "24553", "NAME": "test-only"}
        self.report = {"format": "nova-cloud-backup-v1", "status": "verified", "file": "database.dump",
            "host": "fixture.invalid", "port": 24553, "database": "test-only",
            "created_at": datetime.now(timezone.utc).isoformat(), "bytes": self.archive.stat().st_size,
            "sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        self.save()

    def save(self):
        save_json(self.path, self.report)

    def test_verified_matching_backup(self):
        self.assertEqual(verified_backup(self.path, self.database), self.report)

    def test_incomplete_backup_rejected(self):
        self.report["status"] = "incomplete"
        self.save()
        with self.assertRaisesRegex(RuntimeError, "completo"):
            verified_backup(self.path, self.database)

    def test_wrong_destination_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "destino"):
            verified_backup(self.path, {**self.database, "HOST": "different.invalid"})

    def test_stale_or_future_backup_rejected(self):
        for offset in (timedelta(hours=-3), timedelta(hours=1)):
            self.report["created_at"] = (datetime.now(timezone.utc) + offset).isoformat()
            self.save()
            with self.assertRaisesRegex(RuntimeError, "reciente"):
                verified_backup(self.path, self.database)

    def test_modified_archive_rejected(self):
        self.archive.write_bytes(b"tampered  backup bytes")
        with self.assertRaisesRegex(RuntimeError, "huella"):
            verified_backup(self.path, self.database)

    def test_path_traversal_rejected(self):
        self.report["file"] = "../database.dump"
        self.save()
        with self.assertRaisesRegex(RuntimeError, "completo"):
            verified_backup(self.path, self.database)


class MigrationPlanGateTests(SimpleTestCase):
    def plan(self, names, *, backwards=False, app="mainApp", operation="CreateModel"):
        return [(SimpleNamespace(name=name, app_label=app, operations=[type(operation, (), {})()]), backwards)
                for name in names]

    def check(self, plan):
        with patch("django.db.migrations.executor.MigrationExecutor") as executor:
            executor.return_value.migration_plan.return_value = plan
            return checked_plan(object())

    def test_exact_plan_and_remaining_suffix(self):
        self.assertEqual(self.check(self.plan(EXPECTED)), EXPECTED)
        self.assertEqual(self.check(self.plan(EXPECTED[-2:])), EXPECTED[-2:])
        self.assertEqual(self.check([]), [])

    def test_rollback_or_other_app_rejected(self):
        for plan in (self.plan(EXPECTED, backwards=True), self.plan(EXPECTED, app="another_app")):
            with self.assertRaisesRegex(RuntimeError, "fuera"):
                self.check(plan)

    def test_data_operation_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "operaciones"):
            self.check(self.plan(EXPECTED, operation="RunPython"))

    def test_missing_dependency_or_other_migration_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "orden"):
            self.check(self.plan([EXPECTED[0], EXPECTED[-1]]))
        with self.assertRaisesRegex(RuntimeError, "fuera"):
            self.check(self.plan(["0050_not_authorized"]))
