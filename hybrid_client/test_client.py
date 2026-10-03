"""Pruebas sin red ni dependencias externas: python -m unittest hybrid_client.test_client."""
import json
import re
import tempfile
import threading
import unittest
import sqlite3
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from hybrid_client.client import Client, RemoteError, cloud_url
from hybrid_client.server import make_server
from hybrid_client.store import Store
from pos_shared.protocol import ProtocolError, price_cart


class FakeCloud:
    def __init__(self):
        self.offline = False
        self.lose_ack = False
        self.reject_catalog = False
        self.sales = {}
        self.counter = 0

    def __call__(self, action, data):
        if self.offline:
            raise RemoteError("Red no disponible")
        if action == "enroll":
            return {"device_id": str(uuid4()), "name": "PC prueba", "point": "Caja 1"}
        if action == "session":
            return {"session_id": data["session_id"], "user": "Cajero", "point": "Caja 1", "branch": "Pruebas", "expires_at": (datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()}
        if action == "catalog":
            if self.reject_catalog:
                raise RemoteError("Sesión vencida", 403, "session_expired")
            return {"updates": [{"id": 10, "name": "TOMATE POR GRAMO", "price": "3.80", "stock": -5, "barcode": "770123", "quote": "signed", "digest": "a"*32}], "deleted": [], "more": False, "server_time": datetime.now(timezone.utc).isoformat()}
        if action == "sale":
            if data["operation_id"] not in self.sales:
                self.counter += 1
                self.sales[data["operation_id"]] = {"status": "accepted", "operation_id": data["operation_id"], "sale_id": self.counter, "receipt_text": "COMPROBANTE NUBE"}
            if self.lose_ack:
                self.lose_ack = False
                raise RemoteError("Se perdió la respuesta después de guardar")
            return self.sales[data["operation_id"]]
        if action == "release":
            return {"status": "released"}
        raise AssertionError(action)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cloud = FakeCloud()
        self.client = Client(Store(self.folder.name), transport=self.cloud)
        self.client.enroll({"url": "https://example.test", "code": "test-only"})
        self.client.start({"username": "test", "password": "test", "pin": "clave-local"})

    def sale(self, **kw):
        data = {"operation_id": str(uuid4()), "items": [{"id": 10, "quantity": 500}], "cash_received": "2000"}
        data.update(kw)
        return data

    def test_online_confirms_cloud_and_keeps_receipt(self):
        row = self.client.checkout(self.sale())
        self.assertEqual(row["state"], "accepted")
        self.assertFalse(self.client.store.pending())
        self.assertEqual(row["sale_id"], 1)

    def test_offline_survives_restart_and_syncs_once(self):
        self.cloud.offline = True
        data = self.sale()
        row = self.client.checkout(data)
        self.assertEqual(row["state"], "pending")
        self.assertIn("CAMBIO: $ 100", row["receipt"])
        restarted = Client(Store(self.folder.name), transport=self.cloud)
        with self.assertRaises(ProtocolError):
            restarted.checkout(data)
        restarted.unlock({"pin": "clave-local"})
        self.cloud.offline = False
        restarted.synchronize()
        restarted.synchronize()
        self.assertEqual(self.cloud.counter, 1)
        self.assertFalse(restarted.store.pending())

    def test_lost_ack_retry_does_not_duplicate(self):
        self.cloud.lose_ack = True
        data = self.sale()
        self.assertEqual(self.client.checkout(data)["state"], "pending")
        self.client.synchronize()
        self.assertEqual(self.cloud.counter, 1)
        self.assertEqual(self.client.checkout(data)["state"], "accepted")

    def test_reusing_id_with_different_cart_is_rejected(self):
        data = self.sale()
        self.client.checkout(data)
        data["items"] = [{"id": 10, "quantity": 400}]
        with self.assertRaises(ProtocolError):
            self.client.checkout(data)

    def test_catalog_error_cannot_revert_an_accepted_sale(self):
        self.cloud.offline = True
        data = self.sale()
        self.client.checkout(data)
        self.cloud.offline = False
        self.cloud.reject_catalog = True
        self.client.synchronize()
        self.assertEqual(self.client.store.operation(data["operation_id"])["state"], "accepted")
        self.assertFalse(self.client.store.pending())

    def test_expired_session_cannot_sell_but_can_sync_pending_and_release(self):
        self.cloud.offline = True
        self.client.checkout(self.sale())
        session = self.client.store.get("session")
        session["expires_at"] = (datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
        self.client.store.set("session", session)
        with self.assertRaises(ProtocolError):
            self.client.checkout(self.sale())
        self.cloud.offline = False
        self.client.release()
        self.assertFalse(self.client.store.get("session"))
        self.assertTrue((self.client.store.directory / "backup.sqlite3").exists())

    def test_pending_prevents_release(self):
        self.cloud.offline = True
        self.client.checkout(self.sale())
        with self.assertRaises(ProtocolError):
            self.client.release()

    def test_rejection_is_not_treated_as_offline_permission(self):
        original = self.cloud
        def rejected(action, data):
            if action == "sale":
                raise RemoteError("Permiso retirado", 403)
            return original(action, data)
        self.client.transport = rejected
        self.assertEqual(self.client.checkout(self.sale())["state"], "conflict")
        with self.assertRaises(ProtocolError):
            self.client.checkout(self.sale())
        self.assertEqual(len(self.client.store.pending()), 1)
        self.client.transport = original
        self.client.synchronize(retry_conflicts=True)
        self.assertFalse(self.client.store.pending())
        self.assertIsNone(self.client.store.get("blocked"))
        self.assertEqual(self.cloud.counter, 1)

    def test_new_catalog_total_requires_review_before_saving(self):
        with self.assertRaises(ProtocolError):
            self.client.checkout(self.sale(expected_total="1800"))
        self.assertFalse(self.client.store.pending())

    def test_disk_write_failure_never_sends_the_sale(self):
        with patch.object(self.client.store, "enqueue", side_effect=OSError("Disk full")):
            with self.assertRaises(OSError):
                self.client.checkout(self.sale())
        self.assertEqual(self.cloud.counter, 0)

    def test_disk_failure_saving_ack_is_safe_after_restart(self):
        data = self.sale()
        with patch.object(self.client.store, "mark", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.client.checkout(data)
        self.assertEqual(self.cloud.counter, 1)
        self.assertEqual(self.client.store.operation(data["operation_id"])["state"], "pending")
        restarted = Client(Store(self.folder.name), transport=self.cloud)
        restarted.unlock({"pin": "clave-local"})
        restarted.synchronize()
        self.assertEqual(self.cloud.counter, 1)
        self.assertFalse(restarted.store.pending())

    def test_sqlite_full_rolls_back_whole_operation_without_filling_real_disk(self):
        self.cloud.offline = True
        first = self.client.checkout(self.sale())
        payload = json.loads(self.client.store.operation(first["id"])["payload"])
        payload.update(operation_id=str(uuid4()), sequence=2)
        real_connect = sqlite3.connect
        def constrained(*args, **kwargs):
            db = real_connect(*args, **kwargs)
            pages = db.execute("PRAGMA page_count").fetchone()[0]
            db.execute(f"PRAGMA max_page_count={pages}")
            return db
        with patch("hybrid_client.store.sqlite3.connect", side_effect=constrained):
            with self.assertRaises(sqlite3.OperationalError) as caught:
                self.client.store.enqueue(payload, "x" * 500000)
        self.assertIn("full", str(caught.exception).lower())
        self.assertIsNone(self.client.store.operation(payload["operation_id"]))
        self.assertEqual(self.client.store.last_sequence(payload["session_id"]), 1)
        self.client.store.enqueue(payload, "Comprobante ficticio")
        self.assertEqual(self.client.store.last_sequence(payload["session_id"]), 2)
        self.assertEqual(self.cloud.counter, 0)

    def test_backward_clock_does_not_enqueue_or_send(self):
        self.client.store.set("last_clock", datetime.now(timezone.utc).timestamp() + 180)
        with self.assertRaises(ProtocolError):
            self.client.checkout(self.sale())
        self.assertEqual(self.cloud.counter, 0)
        self.assertFalse(self.client.store.pending())

    def test_forward_clock_past_authorization_does_not_enqueue(self):
        real_datetime = datetime
        with patch("hybrid_client.client.datetime") as clock:
            clock.now.return_value = real_datetime.now(timezone.utc) + timedelta(hours=13)
            clock.fromisoformat.side_effect = real_datetime.fromisoformat
            with self.assertRaises(ProtocolError):
                self.client.checkout(self.sale())
        self.assertEqual(self.cloud.counter, 0)
        self.assertFalse(self.client.store.pending())

    def test_one_process_per_data_directory(self):
        from hybrid_client.__main__ import ProcessLock
        first = ProcessLock(self.folder.name)
        try:
            with self.assertRaises(OSError):
                ProcessLock(self.folder.name)
        finally:
            first.close()

    def test_invalid_quantity_and_cash_do_not_persist(self):
        for qty in (-1, 0, 1.5, True, 1000001):
            with self.subTest(qty=qty), self.assertRaises(ProtocolError):
                self.client.checkout(self.sale(items=[{"id":10,"quantity":qty}]))
        with self.assertRaises(ProtocolError):
            self.client.checkout(self.sale(cash_received="100"))
        self.assertFalse(self.client.store.pending())

    def test_shared_bag_promotion(self):
        _, total = price_cart([{"id": 1, "quantity": 1}, {"id": 7318, "quantity": 2}], {1:{"name":"A","price":"11000"},7318:{"name":"BOLSA","price":"100"}})
        self.assertEqual(str(total), "11000")

    def test_requires_https_without_credentials_or_paths(self):
        for url in ("http://example.test", "https://x:y@example.test", "https://example.test/path", "https://example.test/?token=secret"):
            with self.subTest(url=url), self.assertRaises(ProtocolError):
                cloud_url(url)
        self.assertEqual(cloud_url("http://127.0.0.1:8000", True), "http://127.0.0.1:8000")

    def test_http_security_and_no_device_secret_in_products(self):
        server = make_server(self.client, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with self.assertRaises(OSError):
                make_server(self.client, server.server_port)
            url = f"http://127.0.0.1:{server.server_port}"
            with urlopen(url + "/") as response:
                html = response.read().decode()
            token = re.search(r'name="local-token" content="([^"]+)"', html).group(1)
            self.assertNotIn(self.client.store.get("config")["secret"], html)
            with urlopen(url + "/api/products") as response:
                self.assertNotIn('"quote"', response.read().decode())
            with urlopen(url + "/api/printer") as response:
                self.assertNotIn('"agent_token"', response.read().decode())
            for headers in ({"Host":"evil.example"}, {"Sec-Fetch-Site":"cross-site"}):
                with self.assertRaises(HTTPError) as cm:
                    urlopen(Request(url + "/", headers=headers))
                self.assertEqual(cm.exception.code, 403)
            with self.assertRaises(HTTPError) as cm:
                urlopen(Request(url + "/api/release", data=b"{}", headers={"Content-Type":"application/json","Origin":"https://evil.example"}))
            self.assertEqual(cm.exception.code, 403)
            with self.assertRaises(HTTPError) as cm:
                urlopen(Request(url + "/api/checkout", data=json.dumps({**self.sale(), "session_id":str(uuid4())}).encode(),
                    headers={"Content-Type":"application/json","Origin":url,"X-Local-Token":token}))
            self.assertEqual(cm.exception.code, 400)
            self.assertEqual(self.cloud.counter, 0)
            for path in ("print","printer"):
                with self.assertRaises(HTTPError) as cm:
                    urlopen(Request(url+"/api/"+path,data=json.dumps({"session_id":str(uuid4())}).encode(),
                        headers={"Content-Type":"application/json","Origin":url,"X-Local-Token":token}))
                self.assertEqual(cm.exception.code,400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_closed_browser_does_not_turn_saved_response_into_server_error(self):
        server = make_server(self.client,0)
        try:
            for error in (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):
                handler = MagicMock()
                handler.path = "/api/checkout"
                handler.wfile.write.side_effect = error()
                server.RequestHandlerClass.reply(handler,200,{"saved":True})
                self.assertIs(handler.close_connection,True)
                handler.send_response.assert_called_once_with(200)
        finally:
            server.server_close()


if __name__ == "__main__":
    unittest.main()
