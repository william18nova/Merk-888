"""Impresión simulada: nunca envía trabajos a impresoras reales."""
import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

from hybrid_client.client import Client, NoRedirects
from hybrid_client.printing import (CUT, DRAWER, PrinterError, agent_post,
                                    receipt_text, send_job)
from hybrid_client.store import Store
from hybrid_client.test_client import FakeCloud
from pos_shared.protocol import ProtocolError


class PrintingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cloud = FakeCloud()
        self.client = Client(Store(self.folder.name), transport=self.cloud)
        self.client.enroll({"url":"https://example.test","code":"test-only"})
        self.client.start({"username":"test","password":"test","pin":"clave-local"})
        self.printer = self.client.printer
        self.printer.configure({"pin":"clave-local","backend":"agent","automatic":True,
                                "agent_token":"test-printer-secret","drawer":True})

    def sale(self):
        return {"operation_id":str(uuid4()),"items":[{"id":10,"quantity":500}],"cash_received":"2000"}

    def test_automatic_print_does_not_block_checkout_or_repeat_on_sync(self):
        self.cloud.offline = True
        data = self.sale()
        with patch("hybrid_client.printing.send_job", return_value="Enviado a la cola.") as send:
            result = self.client.checkout(data)
            self.assertEqual(result["printing"]["state"], "queued")
            send.assert_not_called()
            self.assertTrue(self.printer.process_one())
            self.assertFalse(self.printer.process_one())
            self.cloud.offline = False
            self.client.synchronize()
            self.client.checkout(data)
            self.assertFalse(self.printer.process_one())
            send.assert_called_once()
        self.assertEqual(self.cloud.counter, 1)
        payload = json.loads(self.client.store.print_jobs()[0]["payload"])
        self.assertIn("PENDIENTE DE SINCRONIZAR", payload["text"])
        self.assertTrue(payload["drawer"])

    def test_copy_requires_confirmation_and_is_idempotent(self):
        result = self.client.checkout(self.sale())
        request_id = str(uuid4())
        with self.assertRaisesRegex(ProtocolError, "en proceso"):
            self.printer.enqueue(result["id"], request_id=request_id, confirm_copy=True)
        with patch("hybrid_client.printing.send_job",return_value="Enviado"):
            self.printer.process_one()
        with self.assertRaisesRegex(ProtocolError, "Confirma"):
            self.printer.enqueue(result["id"], request_id=request_id)
        first = self.printer.enqueue(result["id"], request_id=request_id, confirm_copy=True)
        again = self.printer.enqueue(result["id"], request_id=request_id, confirm_copy=True)
        self.assertEqual(first, again)
        jobs = self.client.store.print_jobs()
        self.assertEqual(len(jobs),2)
        copy = json.loads(jobs[-1]["payload"])
        self.assertIn("COPIA / REIMPRESION", copy["text"])
        self.assertFalse(copy["drawer"])
        self.assertNotIn("agent_token", copy)
        other = self.client.checkout(self.sale())
        with self.assertRaisesRegex(ProtocolError,"otra venta"):
            self.printer.enqueue(other["id"],request_id=request_id,confirm_copy=True)

    def test_ambiguous_print_is_not_retried(self):
        result = self.client.checkout(self.sale())
        with patch("hybrid_client.printing.send_job",side_effect=PrinterError("No llegó confirmación", uncertain=True)) as send:
            self.printer.process_one()
            self.assertFalse(self.printer.process_one())
            send.assert_called_once()
        row = self.client.public_operation(self.client.store.operation(result["id"]))
        self.assertEqual(row["state"],"accepted")
        self.assertEqual(row["printing"]["state"],"uncertain")
        self.assertEqual(self.cloud.counter,1)

    def test_restart_retains_queue_but_never_repeats_inflight(self):
        first = self.client.checkout(self.sale())
        second = self.client.checkout(self.sale())
        self.client.store.mark_print(first["printing"]["id"],"sending","Enviando")
        restarted = Client(Store(self.folder.name),transport=self.cloud)
        with patch("hybrid_client.printing.send_job", return_value="Enviado") as send:
            restarted.printer.process_one()
            self.assertFalse(restarted.printer.process_one())
            send.assert_called_once()
        self.assertEqual(restarted.store.print_job(first["printing"]["id"])["state"],"uncertain")
        self.assertEqual(restarted.store.print_job(second["printing"]["id"])["state"],"sent")

    def test_print_queue_failure_does_not_undo_sale(self):
        data = self.sale()
        with patch.object(self.printer,"enqueue",side_effect=OSError("Disco lleno")):
            result = self.client.checkout(data)
        self.assertEqual(result["state"],"accepted")
        self.assertEqual(result["printing"]["state"],"failed")
        self.assertEqual(self.client.checkout(data)["state"],"accepted")
        self.assertEqual(self.cloud.counter,1)

    def test_settings_require_pin_and_never_return_token(self):
        with self.assertRaises(ProtocolError):
            self.printer.configure({"pin":"incorrecta","paper":"58"})
        config = self.printer.configure({"pin":"clave-local","paper":"58","agent_token":""})
        self.assertTrue(config["has_token"])
        self.assertNotIn("test-printer-secret",json.dumps(config))
        self.assertEqual(self.printer.config(private=True)["agent_token"],"test-printer-secret")
        self.client.checkout(self.sale())
        with self.assertRaisesRegex(ProtocolError,"terminen"):
            self.printer.configure({"pin":"clave-local","paper":"80"})

    def test_only_current_unlocked_session_can_print(self):
        result = self.client.checkout(self.sale())
        self.client.unlocked = False
        with self.assertRaises(ProtocolError):
            self.printer.enqueue(result["id"],request_id=str(uuid4()))
        self.client.unlocked = True
        session = self.client.store.get("session")
        session["session_id"] = str(uuid4())
        self.client.store.set("session",session)
        with self.assertRaises(ProtocolError):
            self.printer.enqueue(result["id"],request_id=str(uuid4()))

    def test_invalid_settings_rejected_without_shell_commands(self):
        for values in ({"agent_port":8792},{"agent_port":"8787"},{"agent_token":"a\nb"},
                       {"automatic":"yes"},{"printer":"POS;touch hacked"},{"paper":"900"}):
            with self.subTest(values=values), self.assertRaises(ProtocolError):
                self.printer.configure({"pin":"clave-local",**values})
        with patch("hybrid_client.printing.sys.platform","win32"), self.assertRaises(ProtocolError):
            self.printer.configure({"pin":"clave-local","backend":"cups","printer":"POS80"})

    def test_browser_mode_has_no_automatic_side_effects(self):
        config=self.printer.configure({"pin":"clave-local","backend":"browser","automatic":True,"drawer":True})
        self.assertFalse(config["automatic"])
        self.assertFalse(config["drawer"])
        result=self.client.checkout(self.sale())
        self.assertEqual(result["printing"]["state"],"not_requested")
        self.assertFalse(self.client.store.print_jobs())

    def test_sqlite_v1_upgrade_preserves_pending_sales(self):
        self.cloud.offline=True
        result=self.client.checkout(self.sale())
        with self.client.store.connect() as db:
            db.execute("DROP TABLE print_jobs")
            db.execute("PRAGMA user_version=1")
        upgraded=Store(self.folder.name)
        self.assertEqual(upgraded.operation(result["id"])["state"],"pending")
        self.assertEqual(len(upgraded.pending()),1)
        self.assertFalse(upgraded.print_jobs())
        with upgraded.connect() as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],2)
            db.execute("PRAGMA user_version=3")
        with self.assertRaisesRegex(RuntimeError,"más nueva"):
            Store(self.folder.name)


class PrinterTransportTests(unittest.TestCase):
    def payload(self, **kwargs):
        return {"backend":"agent","paper":"80","agent_port":8787,"text":"TOTAL: $ 1900\n", "cut":True,"drawer":False,"printer":"POS80",**kwargs}

    def test_agent_protocol_and_drawer(self):
        with patch("hybrid_client.printing.agent_post") as post:
            send_job(self.payload(drawer=True),"test-token")
            self.assertEqual(post.call_count,2)
            port,token,path,data=post.call_args_list[0].args
            self.assertEqual((port,token,path),(8787,"test-token","print"))
            self.assertTrue(data["text"].endswith(CUT))
            self.assertTrue(data["cut_command_embedded"])
            self.assertEqual(post.call_args_list[1].args[-2:],("kick",{}))
        with patch("hybrid_client.printing.agent_post",side_effect=[None,PrinterError("Cajón")]) as post:
            self.assertIn("No reimprimas",send_job(self.payload(drawer=True),"test-token"))
            self.assertEqual(post.call_count,2)

    def test_agent_uses_loopback_no_proxy_no_redirect(self):
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"ok":true}'
        with patch("hybrid_client.printing.build_opener") as build:
            build.return_value.open.return_value=response
            agent_post(8787,"test-token","print",{"text":"test"})
            proxy,redirect=build.call_args.args
            self.assertEqual(proxy.proxies,{})
            self.assertIsInstance(redirect,NoRedirects)
            req=build.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url,"http://127.0.0.1:8787/print")
            self.assertEqual(req.get_header("X-pos-agent-token"),"test-token")

    def test_agent_transport_failures_are_uncertain_or_rejected(self):
        for error,uncertain in ((URLError("timed out"),True),(HTTPError("local",403,"Forbidden",{},None),False),
                                (HTTPError("local",500,"Error",{},None),True)):
            with self.subTest(error=error),patch("hybrid_client.printing.build_opener") as build:
                build.return_value.open.side_effect=error
                with self.assertRaises(PrinterError) as caught:
                    agent_post(8787,"test-token","print",{})
                self.assertEqual(caught.exception.uncertain,uncertain)
        response=MagicMock()
        response.__enter__.return_value.read.return_value=b'{"success":false}'
        with patch("hybrid_client.printing.build_opener") as build:
            build.return_value.open.return_value=response
            with self.assertRaises(PrinterError) as caught:
                agent_post(8787,"test-token","print",{})
            self.assertTrue(caught.exception.uncertain)

    def test_cups_stdin_no_shell_and_escpos_options(self):
        with patch("hybrid_client.printing.sys.platform","linux"),patch("hybrid_client.printing.shutil.which",return_value="/usr/bin/lp"),patch("hybrid_client.printing.subprocess.run") as run:
            send_job(self.payload(backend="cups",paper="58",drawer=True),"")
            command=run.call_args.args[0]
            self.assertEqual(command[:3],["/usr/bin/lp","-d","POS80"])
            self.assertIn("media=Custom.58x3276mm",command)
            self.assertIs(run.call_args.kwargs["shell"],False)
            data=run.call_args.kwargs["input"]
            self.assertIn(DRAWER,data)
            self.assertTrue(data.endswith(CUT.encode("ascii")))
            run.side_effect=subprocess.TimeoutExpired("lp",8)
            with self.assertRaises(PrinterError) as caught:
                send_job(self.payload(backend="cups"),"")
            self.assertTrue(caught.exception.uncertain)

    def test_format_wraps_width_and_removes_injected_controls(self):
        row={"result":"{}","state":"pending","id":str(uuid4()),"receipt":"PRODUCTO "*10+"\nTOTAL: $ 123456789\n\x1bpAB\x1dVA"}
        for paper,width in (("58",32),("80",48)):
            text=receipt_text(row,paper,copy=True)
            self.assertIn("COPIA / REIMPRESION",text)
            self.assertIn("TOTAL: $ 123456789",text)
            self.assertNotIn("\x1b",text)
            self.assertNotIn("\x1d",text)
            self.assertTrue(all(len(line)<=width for line in text.splitlines()))


if __name__=="__main__":
    unittest.main()
