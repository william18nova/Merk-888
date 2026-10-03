"""Copias con datos ficticios; nunca usa Aiven ni una impresora real."""
import copy
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from hybrid_client.backup import (decrypt, encrypt, read_backup, restore_for_review,
                                  snapshot, summary, validate, write_new)
from hybrid_client.client import Client
from hybrid_client.server import make_server
from hybrid_client.store import Store
from hybrid_client.test_client import FakeCloud
from pos_shared.protocol import ProtocolError

PASSWORD = "copia-ficticia-con-clave-2026"


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "caja")
        self.cloud = FakeCloud()
        self.client = Client(self.store, transport=self.cloud)
        self.client.enroll({"url": "https://example.test", "code": "test"})
        self.client.start({"username": "test", "password": "test", "pin": "clave-local"})
        self.cloud.offline = True
        self.sale = self.client.checkout({"operation_id": str(uuid4()), "items": [{"id": 10, "quantity": 500}], "cash_received": "2000"})

    def test_encrypted_roundtrip_preserves_wal_pending_and_secrets_without_leaking(self):
        # Un lector mantiene WAL abierto para que no baste con copiar el .sqlite3.
        with self.store.connect() as reader:
            reader.execute("BEGIN")
            reader.execute("SELECT * FROM meta").fetchone()
            self.store.set("sentinel", "dato-en-wal")
            document = snapshot(self.store.path)
        raw = encrypt(document, PASSWORD)
        self.assertNotIn(self.store.get("config")["secret"].encode(), raw)
        self.assertNotIn(b"dato-en-wal", raw)
        recovered = decrypt(raw, PASSWORD)
        self.assertEqual(recovered, document)
        self.assertEqual(summary(recovered)["pending"], 1)
        self.assertNotIn("secret", json.dumps(summary(recovered)))

    def test_wrong_password_tampering_and_truncation_are_rejected(self):
        raw = encrypt(snapshot(self.store.path), PASSWORD)
        for value, password in [(raw, "otra-clave-larga-123"), (raw[:-1], PASSWORD),
                                (raw[:-1] + bytes([raw[-1] ^ 1]), PASSWORD), (b"SQLite", PASSWORD)]:
            with self.subTest(length=len(value)), self.assertRaises(ProtocolError):
                decrypt(value, password)
        self.assertEqual(self.store.operation(self.sale["id"])["state"], "pending")

    def test_random_salt_and_nonce_make_different_archives(self):
        document = snapshot(self.store.path)
        self.assertNotEqual(encrypt(document, PASSWORD), encrypt(document, PASSWORD))

    def test_short_or_missing_password_rejected(self):
        document = snapshot(self.store.path)
        for password in (None, "123", "x" * 129):
            with self.assertRaises(ProtocolError):
                encrypt(document, password)

    def test_review_restores_pending_and_never_schedules_prints(self):
        for state in ("queued", "sending", "sent"):
            self.store.enqueue_print(state, self.sale["id"], {"text": "FICTICIO"})
            self.store.mark_print(state, state, "")
        document = decrypt(encrypt(snapshot(self.store.path), PASSWORD), PASSWORD)
        destination = self.root / "recuperacion"
        report = restore_for_review(document, destination)
        self.assertEqual(report["pending"], 1)
        self.assertFalse((destination / "operations.sqlite3").exists())
        db = sqlite3.connect(destination / "recovery.sqlite3")
        try:
            rows = dict(db.execute("SELECT id,state FROM print_jobs"))
            self.assertEqual(rows, {"queued": "uncertain", "sending": "uncertain", "sent": "sent"})
            restored = db.execute("SELECT id,payload,state FROM operations").fetchone()
            self.assertEqual(restored[0], self.sale["id"])
            self.assertEqual(restored[1], self.store.operation(self.sale["id"])["payload"])
            self.assertEqual(restored[2], "pending")
        finally:
            db.close()
        with self.assertRaisesRegex(RuntimeError, "revisión"):
            Store(destination)
        self.assertEqual(self.cloud.counter, 0)

    def test_even_renamed_recovery_database_is_blocked(self):
        destination = self.root / "revision"
        restore_for_review(snapshot(self.store.path), destination)
        (destination / "RECOVERY-REVIEW.json").unlink()
        (destination / "recovery.sqlite3").rename(destination / "operations.sqlite3")
        with self.assertRaisesRegex(RuntimeError, "conciliación"):
            Store(destination)

    def test_refuses_to_replace_existing_folder_or_backup(self):
        original = self.store.path.read_bytes()
        with self.assertRaises(ProtocolError):
            restore_for_review(snapshot(self.store.path), self.store.directory)
        self.assertEqual(original, self.store.path.read_bytes())
        target = self.root / "copia.novabackup"
        write_new(target, b"anterior")
        with self.assertRaises(ProtocolError):
            write_new(target, b"nueva")
        self.assertEqual(target.read_bytes(), b"anterior")

    def test_invalid_structures_do_not_create_recovery_directory(self):
        original = snapshot(self.store.path)
        for case in ("duplicate", "sequence", "state", "table", "json", "receipt_ack"):
            document = copy.deepcopy(original)
            row = document["tables"]["operations"][0]
            if case == "duplicate": document["tables"]["operations"].append(copy.deepcopy(row))
            if case == "sequence": row["sequence"] = 9
            if case == "state": row["state"] = "anything"
            if case == "table": document["tables"]["malicious; DROP TABLE operations"] = []
            if case == "json": row["payload"] = "not json"
            if case == "receipt_ack": row["state"] = "accepted"
            destination = self.root / case
            with self.subTest(case=case), self.assertRaises(ProtocolError):
                restore_for_review(document, destination)
            self.assertFalse(destination.exists())

    def test_accepted_pending_conflict_and_all_sessions_are_kept(self):
        self.cloud.offline = False
        self.client.synchronize()
        self.cloud.offline = True
        second = self.client.checkout({"operation_id": str(uuid4()), "items": [{"id": 10, "quantity": 1}], "cash_received": "4"})
        self.store.mark(second["id"], "conflict", error="Prueba ficticia")
        document = snapshot(self.store.path)
        info = summary(document)
        self.assertEqual((info["operations"], info["accepted"], info["conflict"]), (2, 1, 1))
        # Respaldar no cambia los UUID ni fuerza reenvíos/aceptaciones.
        self.assertEqual(self.cloud.counter, 1)

    def test_atomic_local_backup_keeps_previous_if_replacement_fails(self):
        target = self.store.backup()
        original = target.read_bytes()
        self.store.set("after_backup", True)
        with patch("hybrid_client.store.os.replace", side_effect=OSError("falla simulada")):
            with self.assertRaises(OSError):
                self.store.backup()
        self.assertEqual(target.read_bytes(), original)
        self.assertFalse(list(self.store.directory.glob(".backup-*")))

    def test_missing_source_does_not_create_database(self):
        path = self.root / "no-existe.sqlite3"
        with self.assertRaises(ProtocolError):
            snapshot(path)
        self.assertFalse(path.exists())

    def test_requires_correct_pin_and_matching_different_password(self):
        for data in ({"pin": "wrong", "password": PASSWORD, "confirmation": PASSWORD},
                     {"pin": "clave-local", "password": PASSWORD, "confirmation": "no-coincide"},
                     {"pin": "clave-local", "password": "clave-local", "confirmation": "clave-local"}):
            with self.assertRaises(ProtocolError):
                self.client.export_backup(data)
        self.client.unlocked = False
        with self.assertRaises(ProtocolError):
            self.client.export_backup({"pin": "clave-local", "password": PASSWORD, "confirmation": PASSWORD})

    def test_cli_exports_verifies_and_recovers_without_opening_active_store(self):
        from hybrid_client.__main__ import main
        output = self.root / "copia.novabackup"
        destination = self.root / "cli-recovery"
        def run(arguments, prompts):
            with patch("sys.argv", ["NovaPOS", *arguments]), patch("getpass.getpass", side_effect=prompts), patch("sys.stdout", new_callable=io.StringIO) as out:
                main()
                self.assertNotIn(PASSWORD, out.getvalue())
        run(["--data-dir", str(self.store.directory), "--export-backup", str(output)], [PASSWORD, PASSWORD])
        run(["--verify-backup", str(output)], [PASSWORD])
        run(["--restore-backup", str(output), "--recovery-dir", str(destination)], [PASSWORD])
        self.assertEqual(summary(read_backup(output, PASSWORD))["operations"], 1)
        self.assertTrue((destination / "recovery.sqlite3").exists())
        if os.name != "nt":
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(destination.stat().st_mode & 0o777, 0o700)

    def test_local_http_backup_checks_origin_session_and_unlock(self):
        server = make_server(self.client, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with urlopen(base) as response:
            token = re.search(r'name="local-token" content="([^"]+)"', response.read().decode())[1]
        data = {"session_id": self.store.get("session")["session_id"], "pin": "clave-local", "password": PASSWORD, "confirmation": PASSWORD}
        def request(body, origin=base):
            return urlopen(Request(base + "/api/backup", data=json.dumps(body).encode() if origin == base else b"", headers={"Origin": origin, "X-Local-Token": token, "Content-Type": "application/json"}), timeout=10)
        with self.assertRaises(HTTPError) as error:
            request(data, "https://other.test")
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            request({**data, "session_id": "otra"})
        self.assertEqual(error.exception.code, 400)
        with request(data) as response:
            self.assertEqual(response.headers["Content-Type"], "application/octet-stream")
            self.assertEqual(summary(decrypt(response.read(), PASSWORD))["pending"], 1)
        self.client.unlocked = False
        with self.assertRaises(HTTPError) as error:
            request(data)
        self.assertEqual(error.exception.code, 401)
