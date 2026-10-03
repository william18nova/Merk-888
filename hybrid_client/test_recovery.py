"""Fallos del disco/transporte de recuperación, solo fixtures sin nube real."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from hybrid_client.backup import restore_for_review, snapshot
from hybrid_client.client import Client, RemoteError
from hybrid_client.recovery import Recovery
from hybrid_client.store import Store
from hybrid_client.test_client import FakeCloud
from pos_shared.protocol import ProtocolError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        store=Store(self.root/"original")
        cloud=FakeCloud(); local=Client(store,transport=cloud)
        local.enroll({"url":"https://example.test","code":"demo"})
        local.start({"username":"demo","password":"test","pin":"clave-local"})
        cloud.offline=True
        self.row=local.checkout({"operation_id":str(uuid4()),"items":[{"id":10,"quantity":500}],"cash_received":"2000"})
        store.enqueue_print("unconfirmed",self.row["id"],{"text":"ficticio"})
        self.review=self.root/"review"
        restore_for_review(snapshot(store.path),self.review)
        self.rec_id=str(uuid4()); self.approved=False
        self.client=Recovery(self.review,"https://example.test",transport=self.transport)

    def transport(self, action, data):
        if action=="prepare": return {"recovery_id":self.rec_id,"state":"review","summary":{}}
        if not self.approved: return {"state":"review"}
        return {"state":"completed","recovery_id":self.rec_id,"device_id":self.client.bundle["device_id"],
                "name":"PC ficticio","point":"Pruebas","summary":{},"acknowledgements":{
                    self.row["id"]:{"operation_id":self.row["id"],"status":"accepted","sale_id":1,"receipt_text":"RECIBO FICTICIO"}}}

    def prepare(self):
        self.client.prepare(self.rec_id+".codigo-ficticio")

    def test_waits_for_review_then_requires_fresh_login_and_never_reprints(self):
        self.prepare()
        self.assertEqual(self.client.finish()["state"],"review")
        self.assertFalse((self.review/"pos-recuperado").exists())
        self.approved=True
        result=self.client.finish(); fresh=Store(result["directory"])
        self.assertFalse(fresh.get("session")); self.assertFalse(fresh.get("ready"))
        self.assertFalse(fresh.get("pin")); self.assertFalse(fresh.get("printer"))
        self.assertFalse(fresh.pending())
        self.assertEqual(fresh.print_job("unconfirmed")["state"],"uncertain")
        with self.assertRaises(ProtocolError): Client(fresh).checkout({})
        self.assertEqual(self.client.finish()["directory"],result["directory"])

    def test_disk_failure_does_not_publish_partial_pos_and_retry_is_safe(self):
        self.prepare(); self.approved=True
        secret=self.client.state["secret"]
        with patch("hybrid_client.recovery.os.rename",side_effect=OSError("disco lleno")):
            with self.assertRaises(OSError): self.client.finish()
        self.assertFalse((self.review/"pos-recuperado").exists())
        self.assertFalse(list(self.review.glob(".preparando-pos-*")))
        self.client=Recovery(self.review,"https://example.test",transport=self.transport)
        result=self.client.finish()
        self.assertEqual(Store(result["directory"]).get("config")["secret"],secret)

    def test_lost_prepare_ack_reuses_persisted_secret(self):
        def lose(action,data): raise RemoteError("red perdida")
        self.client.transport=lose
        with self.assertRaises(RemoteError): self.prepare()
        secret=self.client.state["secret"]
        self.client=Recovery(self.review,"https://example.test",transport=self.transport)
        self.prepare()
        self.assertEqual(self.client.state["secret"],secret)

    def test_unexpected_ack_or_missing_ack_cannot_activate(self):
        self.prepare(); self.approved=True
        expected=self.transport("finish",{})
        for case in ("identity","missing","state"):
            result=copy.deepcopy(expected)
            if case=="identity":result["recovery_id"]=str(uuid4())
            if case=="missing":result["acknowledgements"]={}
            if case=="state":result["acknowledgements"][self.row["id"]]["status"]="pending"
            self.client.transport=lambda *_,result=result:result
            with self.subTest(case=case),self.assertRaises(ProtocolError):self.client.finish()
            self.assertFalse((self.review/"pos-recuperado").exists())

    def test_refuses_to_replace_existing_pos(self):
        self.prepare(); self.approved=True
        destination=self.review/"pos-recuperado"
        existing=Store(destination);existing.set("sentinel","conservar")
        with self.assertRaises(ProtocolError):self.client.finish()
        self.assertEqual(existing.get("sentinel"),"conservar")

    def test_existing_empty_destination_is_not_initialized(self):
        self.prepare();self.approved=True
        destination=self.review/"pos-recuperado"
        destination.mkdir()
        with self.assertRaises(ProtocolError):self.client.finish()
        self.assertEqual(list(destination.iterdir()),[])

    def test_cannot_send_to_a_different_server_or_unreviewed_folder(self):
        for path,url in ((self.review,"https://other.test"),(self.root/"original","https://example.test")):
            with self.assertRaises(ProtocolError):Recovery(path,url,transport=self.transport)

    def test_modified_source_after_first_request_is_rejected(self):
        self.prepare()
        with closing(sqlite3.connect(self.review/"recovery.sqlite3")) as db:
            with db:
                payload=json.loads(db.execute("SELECT payload FROM operations").fetchone()[0])
                payload["cash_received"]="4000"
                db.execute("UPDATE operations SET payload=?",(json.dumps(payload),))
        with self.assertRaises(ProtocolError):Recovery(self.review,"https://example.test",transport=self.transport)

    def test_reissued_code_generates_another_replacement_secret(self):
        self.prepare();old=self.client.state["secret"]
        self.rec_id=str(uuid4());self.prepare()
        self.assertNotEqual(old,self.client.state["secret"])
