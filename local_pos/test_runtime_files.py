"""Validación del respaldo sin Django, PostgreSQL ni acceso a la nube."""
import hashlib
import json
from pathlib import Path
import platform
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4
import zipfile

from local_pos import runtime


class RuntimeFileTests(unittest.TestCase):
    def test_incomplete_transition_blocks_start_before_opening_postgres(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("handoff-intent.json", "next-session-intent.json"):
                marker = root / name
                marker.write_text("{}", encoding="utf-8")
                with patch.object(runtime, "installed_state", return_value={}), patch.object(runtime, "Postgres") as postgres:
                    with self.assertRaisesRegex(RuntimeError, "incompleto"):
                        runtime.serve(root)
                    postgres.assert_not_called()
                marker.unlink()

    def test_backup_and_restore_keep_empty_postgres_directories(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "installation"
            runtime.private_dir(root)
            for name in ("postgres/pg_notify", "postgres/pg_tblspc", "postgres/base/123"):
                (root / name).mkdir(parents=True)
            (root / "postgres/base/123/data").write_bytes(b"fictitious pending operation")
            runtime.save_json(root / "local.json", {"password": "test-only"})
            state = {"instance_id": str(uuid4()), "package_id": "candidate", "pg_bin": str(base)}
            target = base / "backups" / "snapshot.zip"
            with patch.object(runtime, "installed_state", return_value=state), patch.object(runtime, "Postgres") as pg:
                pg.return_value.status.return_value = False
                runtime.backup(root, target)
                root.rename(base / "preserved")
                with patch.object(runtime, "package_manifest", return_value={"package_id": "candidate"}):
                    runtime.restore(root, target, confirm_instance=state["instance_id"])
            self.assertTrue((root / "postgres/pg_notify").is_dir())
            self.assertTrue((root / "postgres/pg_tblspc").is_dir())
            self.assertEqual((root / "postgres/base/123/data").read_bytes(), b"fictitious pending operation")

    def test_unsafe_backup_directory_rejected_before_creating_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "installation"
            identity = str(uuid4())
            for directory in ("../escape", "/absolute", "C:/other", "postgres\\escape"):
                archive_path = base / "bad.zip"
                manifest = {"format": runtime.FORMAT + "-backup", "original_root": str(root),
                            "system": platform.system(), "instance_id": identity, "package_id": "candidate",
                            "directories": [directory], "files": {"local.json": hashlib.sha256(b"{}").hexdigest()}}
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr("backup-manifest.json", json.dumps(manifest))
                    archive.writestr("local.json", b"{}")
                with patch.object(runtime, "package_manifest", return_value={"package_id": "candidate"}):
                    with self.assertRaisesRegex(RuntimeError, "inseguro"):
                        runtime.restore(root, archive_path, confirm_instance=identity)
                self.assertFalse(root.exists())
