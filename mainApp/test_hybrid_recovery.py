import copy
import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from hybrid_client.backup import snapshot, restore_for_review
from hybrid_client.client import Client as LocalClient, RemoteError
from hybrid_client.recovery import Recovery
from hybrid_client.store import Store
from pos_shared.protocol import ProtocolError, fingerprint
from .models import RecuperacionHibrida, EquipoHibrido, OperacionHibrida, SesionHibrida, Venta, Rol
from .services import hybrid, hybrid_recovery as recovery
from .test_hybrid import HybridFixture


@override_settings(SECRET_KEY="hybrid-test-key-no-production", ALLOWED_HOSTS=["testserver"])
class HybridRecoveryTests(HybridFixture, TestCase):
    def bundle(self, *rows):
        return {"device_id": str(self.device.pk), "backup_created_at": timezone.now().isoformat(), "operations": list(rows)}

    def issue(self):
        return recovery.authorize(actor=self.user, device_id=self.device.pk, reason="Equipo averiado en pruebas", isolated=True)

    def present(self, bundle):
        record, code = self.issue()
        data = {"code": code, "secret": "b"*64, "manifest": bundle}
        self.assertEqual(recovery.prepare(data)["state"], "review")
        return record, {"recovery_id": str(record.pk), "secret": "b"*64, "manifest": bundle}

    def test_reconciles_lost_ack_pending_and_retires_old_access(self):
        first = self.payload()
        hybrid.accept_sale(self.device, first)
        second = self.payload(sequence=2)
        record, data = self.present(self.bundle({"state": "pending", "payload": first}, {"state": "pending", "payload": second}))
        self.assertEqual(recovery.finish(data)["state"], "review")
        self.assertEqual(Venta.objects.count(), 1)
        with self.assertRaises(hybrid.HybridError):
            hybrid.authenticate_device(f"Bearer {self.device.pk}.{self.secret}")
        recovery.approve(actor=self.user, recovery_id=record.pk, understood=True)
        result = recovery.finish(data)
        self.assertEqual(result["summary"]["already_received"], 1)
        self.assertEqual(result["summary"]["to_upload"], 1)
        self.assertEqual(recovery.finish(data), result)
        self.assertEqual(Venta.objects.count(), 2)
        self.assertEqual(OperacionHibrida.objects.count(), 2)
        self.stock.refresh_from_db(); self.point.refresh_from_db()
        self.assertEqual(self.stock.cantidad, -1000)
        self.assertEqual(self.point.dinerocaja, Decimal("3800"))
        self.assertFalse(SesionHibrida.objects.filter(liberada_en__isnull=True).exists())
        fresh = hybrid.authenticate_device(f"Bearer {self.device.pk}.{'b'*64}")
        hybrid.start_session(fresh, {"username": "hibrido", "password": "testing-pass", "session_id": str(uuid4())})
        record.refresh_from_db()
        self.assertEqual(record.creada_por_id, self.user.pk)
        self.assertEqual(record.aprobada_por_id, self.user.pk)
        self.assertIsNotNone(record.completada_en)

    def test_request_authenticated_before_rotation_is_rejected_inside_lock(self):
        stale = hybrid.authenticate_device(f"Bearer {self.device.pk}.{self.secret}")
        self.issue()
        for action, data in ((hybrid.accept_sale, self.payload()), (hybrid.catalog, {"session_id": self.session["session_id"]}),
                             (hybrid.release_session, {"session_id": self.session["session_id"], "sequence": 0}),
                             (hybrid.start_session, {"session_id":str(uuid4()), "username":"hibrido", "password":"testing-pass"})):
            with self.subTest(action=action.__name__), self.assertRaises(hybrid.HybridError) as error:
                action(stale, data)
            self.assertEqual(error.exception.code, "authentication")
        self.assertFalse(Venta.objects.exists())

    def test_any_failed_sale_rolls_back_whole_batch_and_new_access(self):
        first, second = self.payload(), self.payload(sequence=2)
        second["cash_received"] = "1"
        record, data = self.present(self.bundle({"state":"pending", "payload":first}, {"state":"pending", "payload":second}))
        recovery.approve(actor=self.user, recovery_id=record.pk, understood=True)
        with self.assertRaises(hybrid.HybridError):
            recovery.finish(data)
        self.assertFalse(Venta.objects.exists())
        self.stock.refresh_from_db(); self.point.refresh_from_db(); self.device.refresh_from_db(); record.refresh_from_db()
        self.assertEqual(self.stock.cantidad, 0)
        self.assertEqual(self.point.dinerocaja, 0)
        self.assertEqual(self.device.token_hash, "")
        self.assertEqual(record.estado, "approved")
        self.assertTrue(SesionHibrida.objects.filter(liberada_en__isnull=True).exists())

    def test_changed_payload_or_secret_after_prepare_is_rejected(self):
        record, data = self.present(self.bundle({"state":"pending", "payload":self.payload()}))
        recovery.approve(actor=self.user, recovery_id=record.pk, understood=True)
        changed = copy.deepcopy(data)
        changed["manifest"]["operations"][0]["payload"]["cash_received"] = "3000"
        for payload in (changed, {**data, "secret":"c"*64}):
            with self.assertRaises(hybrid.HybridError):
                recovery.finish(payload)
        self.assertFalse(Venta.objects.exists())

    def test_wrong_device_gaps_duplicates_and_falsely_accepted_do_not_prepare(self):
        for case in ("device", "gap", "duplicate", "accepted"):
            record, code = self.issue()
            payload = self.payload(sequence=2 if case == "gap" else 1)
            bundle = self.bundle({"state":"accepted" if case == "accepted" else "pending", "payload":payload})
            if case == "device": bundle["device_id"] = str(uuid4())
            if case == "duplicate": bundle["operations"].append(copy.deepcopy(bundle["operations"][0]))
            with self.subTest(case=case), self.assertRaises(hybrid.HybridError):
                recovery.prepare({"code":code,"secret":"b"*64,"manifest":bundle})
            record.refresh_from_db(); self.assertEqual(record.estado,"issued")
        self.assertFalse(Venta.objects.exists())

    def test_foreign_uuid_and_cloud_payload_mismatch_are_not_overwritten(self):
        payload = self.payload()
        hybrid.accept_sale(self.device, payload)
        payload["cash_received"] = "3000"
        record, code = self.issue()
        with self.assertRaises(hybrid.HybridError):
            recovery.prepare({"code":code,"secret":"b"*64,"manifest":self.bundle({"state":"pending","payload":payload})})
        self.assertEqual(Venta.objects.count(), 1)

    def test_cloud_newer_than_backup_is_kept_not_replayed(self):
        first, second = self.payload(), self.payload(sequence=2)
        hybrid.accept_sale(self.device, first)
        hybrid.accept_sale(self.device, second)
        record, data = self.present(self.bundle({"state":"pending", "payload":first}))
        recovery.approve(actor=self.user, recovery_id=record.pk, understood=True)
        result = recovery.finish(data)
        self.assertEqual(result["summary"]["cloud_only"], 1)
        self.assertEqual(result["summary"]["to_upload"], 0)
        self.assertEqual(Venta.objects.count(), 2)

    def test_expired_reissued_and_unapproved_authorizations_do_not_activate(self):
        record, data = self.present(self.bundle())
        RecuperacionHibrida.objects.filter(pk=record.pk).update(vence_en=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(hybrid.HybridError): recovery.finish(data)
        self.issue()
        record.refresh_from_db(); self.assertEqual(record.estado, "superseded")
        with self.assertRaises(hybrid.HybridError): recovery.finish(data)
        self.assertTrue(SesionHibrida.objects.filter(liberada_en__isnull=True).exists())

    def test_inactive_approver_blocks_finish(self):
        record, data = self.present(self.bundle())
        recovery.approve(actor=self.user, recovery_id=record.pk, understood=True)
        self.user.is_active = False; self.user.save()
        with self.assertRaises(hybrid.HybridError): recovery.finish(data)

    def test_admin_page_requires_password_and_confirmations_and_shows_audit(self):
        self.client.force_login(self.user)
        url = "/configuracion/equipos-hibridos/"
        data = {"action":"recover", "device":str(self.device.pk), "reason":"Prueba de recuperación", "isolated":"on", "password":"wrong"}
        self.assertEqual(self.client.post(url,data).status_code,400)
        self.assertFalse(RecuperacionHibrida.objects.exists())
        data["password"]="testing-pass"
        response = self.client.post(url,data)
        self.assertEqual(response.status_code,200, response.content)
        self.assertContains(response,"Código de recuperación")
        self.assertContains(response,"Prueba de recuperación")
        self.user.rolid = Rol.objects.create(nombre="Cajero"); self.user.save()
        # El middleware puede redirigir antes de que la vista devuelva su 403.
        self.assertIn(self.client.post(url,data).status_code,(302,403))
        self.assertEqual(RecuperacionHibrida.objects.count(),1)

    def test_recovery_api_does_not_accept_cookie_as_recovery_credential(self):
        record, code = self.issue()
        self.client.force_login(self.user)
        url = "/api/hybrid/v1/recovery/prepare/"
        data = {"code":"wrong", "secret":"b"*64,"manifest":self.bundle()}
        self.assertEqual(self.client.post(url,json.dumps(data),content_type="application/json").status_code,401)
        data["code"] = code
        self.client.logout()  # El reemplazo no tiene una sesión web/cookie.
        response=self.client.post(url,json.dumps(data),content_type="application/json")
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn(b'"secret"',response.content)

    def test_client_full_recovery_lost_responses_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/"original")
            store.set("config",{"url":"https://example.test","device_id":str(self.device.pk),"secret":self.secret})
            store.set("session",self.session);store.set("ready",True)
            store.merge_catalog({"updates":[self.product_data],"deleted":[]})
            local=LocalClient(store,transport=lambda *_: (_ for _ in ()).throw(RemoteError("offline")))
            local.unlocked=True
            row=local.checkout({"operation_id":str(uuid4()),"items":[{"id":self.product.pk,"quantity":500}],"cash_received":"2000"})
            review=Path(temp)/"revision"
            restore_for_review(snapshot(store.path),review)
            record,code=self.issue()
            lost={"prepare":True,"finish":True}
            def transport(action,data):
                result={"prepare":recovery.prepare,"finish":recovery.finish}[action](data)
                if lost[action]:
                    lost[action]=False
                    raise RemoteError("Se perdió respuesta")
                return result
            client=Recovery(review,"https://example.test",transport=transport)
            with self.assertRaises(RemoteError): client.prepare(code)
            client=Recovery(review,"https://example.test",transport=transport)
            client.prepare(code)
            recovery.approve(actor=self.user,recovery_id=record.pk,understood=True)
            with self.assertRaises(RemoteError): client.finish()
            self.assertEqual(Venta.objects.count(),1)
            self.assertFalse((review/"pos-recuperado").exists())
            client=Recovery(review,"https://example.test",transport=transport)
            result=client.finish()
            fresh=Store(result["directory"])
            self.assertFalse(fresh.get("session"));self.assertFalse(fresh.get("pin"));self.assertFalse(fresh.get("printer"))
            self.assertFalse(fresh.pending())
            self.assertEqual(fresh.operation(row["id"])["state"],"accepted")
            self.assertNotEqual(fresh.get("config")["secret"],self.secret)
            self.assertEqual(client.finish()["directory"],result["directory"])
            self.assertEqual(Venta.objects.count(),1)
            self.assertEqual(store.operation(row["id"])["state"],"pending")
            with self.assertRaises(RuntimeError): Store(review)

    def test_completed_recovery_is_idempotent(self):
        record,data=self.present(self.bundle({"state":"pending","payload":self.payload()}))
        recovery.approve(actor=self.user,recovery_id=record.pk,understood=True)
        result=recovery.finish(data)
        self.assertEqual(recovery.finish(data),result)
        self.assertEqual(Venta.objects.count(),1)
