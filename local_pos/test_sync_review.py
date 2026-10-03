import json
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from hybrid_client.client import RemoteError
from mainApp.models import Usuario, Rol, Venta, Inventario
from mainApp.test_hybrid import HybridFixture
from pos_shared.protocol import ProtocolError
from .models import LocalCommand, LocalNode, ReplicaState, LocalSaleSession
from . import sync_review as review
from .sales import checkout, flush
from .test_sales import Remote, LocalSalesTests


@override_settings(HYBRID_LOCAL_SALES_ENABLED=True, HYBRID_REPLICA_MIN_INTERVAL=0)
class ReviewIntegrationTests(HybridFixture, TransactionTestCase):
    databases = {"default", "replica", "replica2"}
    prepare_local = LocalSalesTests.prepare_local
    data = LocalSalesTests.data
    sell = LocalSalesTests.sell

    def setUp(self):
        super().setUp()
        from mainApp.models import MetodoPago
        MetodoPago.objects.filter(pk="efectivo").update(es_efectivo=True)
        self.remote = Remote(self.device, self.session, self.secret)
        self.actor = self.prepare_local("replica", self.remote)

    def rejected(self, code="product", status=409):
        with patch.object(self.remote, "call", side_effect=RemoteError("SECRET-DO-NOT-RENDER", status, code)):
            self.sell()
        return LocalCommand.objects.using("replica").get()

    def retry(self, row, **kwargs):
        return review.retry_conflict(self.actor, row.pk, request_id=kwargs.pop("request_id", uuid4()),
            note="Revisé el producto en la nube.", remote_factory=lambda: self.remote, using="replica", **kwargs)

    def test_safe_retry_preserves_payload_author_stock_and_uuid_then_duplicate_post_is_noop(self):
        row = self.rejected()
        payload, signature, author = row.payload.copy(), row.fingerprint, row.actor_id
        request_id = uuid4()
        self.assertEqual(self.retry(row, request_id=request_id), "accepted")
        row.refresh_from_db(using="replica")
        self.assertEqual((row.payload, row.fingerprint, row.actor_id), (payload, signature, author))
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -500)
        self.assertEqual(row.local_result["review_history"][0]["outcome"], "accepted")
        with patch.object(self.remote, "call") as call:
            self.assertEqual(self.retry(row, request_id=request_id), "accepted")
            call.assert_not_called()
        self.assertEqual(Venta.objects.count(), 1)

    def test_retry_still_rejected_keeps_audit_without_leaking_remote_error(self):
        row = self.rejected("method")
        with patch.object(self.remote, "call", side_effect=RemoteError("SECRET-DO-NOT-RENDER", 409, "method")):
            self.assertEqual(self.retry(row), "conflict")
        row.refresh_from_db(using="replica")
        self.assertEqual(row.local_result["sync"]["code"], "method")
        self.assertEqual(row.local_result["sync"]["attempts"], 2)
        self.assertEqual(row.local_result["review_history"][0]["actor_id"], self.actor.pk)
        self.assertNotIn("SECRET-DO-NOT-RENDER", json.dumps(row.local_result))
        self.assertEqual(Venta.objects.count(), 0)

    def test_cashier_cannot_release_conflict(self):
        row = self.rejected()
        self.actor.rolid = Rol.objects.using("replica").create(nombre="Cajero")
        self.actor.save(using="replica", update_fields=["rolid"])
        with self.assertRaises(PermissionDenied):
            self.retry(row)
        row.refresh_from_db(using="replica")
        self.assertEqual(row.state, "conflict")

    def test_cannot_skip_earlier_operation(self):
        self.remote.offline = True
        self.sell(); self.sell()
        rows = list(LocalCommand.objects.using("replica").order_by("sequence"))
        LocalCommand.objects.using("replica").update(state="conflict")
        with self.assertRaisesRegex(ProtocolError, "anterior"):
            self.retry(rows[1])
        self.assertFalse(LocalCommand.objects.using("replica").filter(state="accepted").exists())

    def test_transport_preflight_failure_does_not_release_or_audit(self):
        row = self.rejected()
        with override_settings(HYBRID_LOCAL_SALES_ENABLED=False), self.assertRaises(ProtocolError):
            self.retry(row)
        with self.assertRaises(OSError):
            review.retry_conflict(self.actor, row.pk, request_id=uuid4(), note="Revisé el permiso",
                remote_factory=lambda: (_ for _ in ()).throw(OSError("test")), using="replica")
        row.refresh_from_db(using="replica")
        self.assertEqual(row.state, "conflict")
        self.assertFalse(row.local_result.get("review_history"))

    def test_unknown_error_is_sanitized_and_expired_auth_does_not_hide_history(self):
        row = self.rejected("http://secret.invalid/TOKEN")
        state = ReplicaState.objects.using("replica").get()
        state.expires_at = timezone.now()-timedelta(days=1)
        state.blocked = True
        state.save(using="replica")
        self.assertEqual(row.local_result["sync"]["code"], "unknown")
        self.assertEqual(review.visible_commands(self.actor, using="replica").count(), 1)
        self.assertTrue(review.connection_context(self.actor, using="replica")[0].blocked)

    def test_network_loss_after_remote_commit_retries_without_duplicate(self):
        original = self.remote.call
        def lose(action, payload):
            original(action, payload)
            raise RemoteError("lost test response")
        with patch.object(self.remote, "call", side_effect=lose):
            result = self.sell()
        self.assertEqual(result["state"], "pending")
        self.assertEqual(Venta.objects.count(), 1)
        review.synchronize_pending(self.actor, lambda: self.remote, using="replica")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(LocalCommand.objects.using("replica").get().state, "accepted")


@override_settings(ALLOWED_HOSTS=["testserver", "127.0.0.1"], HYBRID_LOCAL_SALES_ENABLED=True)
class ReviewPageTests(TransactionTestCase):
    def setUp(self):
        from scripts.full_local_lab import seed
        from django.core.cache import cache
        cache.clear()
        seed()
        self.user = Usuario.objects.get(nombreusuario="laboratorio")
        self.other = Usuario.objects.create_user("otro-local", "test-only")
        self.client.force_login(self.user)
        self.node = LocalNode.objects.get()
        self.session_id = uuid4()
        self.state = ReplicaState.objects.create(node=self.node, local_user=self.user,
            server="https://cloud.example.test", device_id=uuid4(), active_id=uuid4(), scope={},
            expires_at=timezone.now()+timedelta(hours=5))
        LocalSaleSession.objects.create(node=self.node, session_id=self.session_id, data={}, products={})
        self.row = self.create(self.user, 1, "conflict")
        self.hidden = self.create(self.other, 2, "pending")

    def create(self, actor, sequence, state):
        return LocalCommand.objects.create(node=self.node, actor=actor, operation_id=uuid4(), sequence=sequence,
            state=state, kind="expense.create.v1", payload={"session_id":str(self.session_id), "quote":"SECRET-QUOTE"},
            fingerprint="0"*64, local_result={"total":"1004", "concept":"PAGO FICTICIO", "method":"nequi", "base":"1000", "tax":"4",
                "sync":{"code":"method", "attempts":1}, "error":"SECRET-REMOTE"})

    def test_page_and_detail_render_with_original_navigation(self):
        response = self.client.get(reverse("local_sync"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sincronización")
        self.assertContains(response, "Todos los autores de este equipo")
        self.assertContains(response, "$ 1.004,00")
        detail = self.client.get(reverse("local_sync_detail", args=[self.row.pk]))
        self.assertContains(detail, "Verificar de nuevo en la nube")
        self.assertContains(detail, "No se borra")
        self.assertNotContains(detail, "SECRET-")
        self.assertEqual(detail.headers["Cache-Control"], "no-store")

    def test_non_manager_sees_only_own_rows_and_cannot_read_or_retry_another(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("local_sync"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["counts"]["total"], 1)
        self.assertNotContains(response, str(self.row.pk))
        self.assertEqual(self.client.get(reverse("local_sync_detail", args=[self.row.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("local_sync_retry", args=[self.row.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("local_sync_retry", args=[self.hidden.pk])).status_code, 403)

    def test_authentication_and_active_user_required(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("local_sync")).status_code, 302)
        from django.test import RequestFactory
        self.other.is_active = False
        with self.assertRaises(PermissionDenied):
            review.visible_commands(self.other)

    def test_filter_pagination_and_invalid_dates(self):
        response = self.client.get(reverse("local_sync"), {"state":"conflict", "kind":"expense.create.v1", "q":str(self.row.pk)})
        self.assertEqual(response.context["page"].paginator.count, 1)
        invalid = self.client.get(reverse("local_sync"), {"since":"2026-10-03", "until":"2026-10-01"})
        self.assertContains(invalid, "La fecha inicial")
        self.assertEqual(invalid.context["page"].paginator.count, 0)
        for sequence in range(3, 25): self.create(self.user, sequence, "accepted")
        response = self.client.get(reverse("local_sync"), {"page":2})
        self.assertEqual(len(response.context["rows"]), 4)

    def test_csrf_and_post_only_no_get_side_effects(self):
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.user)
        for url in (reverse("local_sync_send"), reverse("local_sync_retry", args=[self.row.pk])):
            self.assertEqual(secure.post(url).status_code, 403)
            self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(reverse("local_sync_send"), HTTP_ORIGIN="https://evil.invalid").status_code, 409)

    def test_expired_or_blocked_replica_does_not_block_review_page(self):
        self.state.blocked = True
        self.state.expires_at = timezone.now()-timedelta(days=1)
        self.state.save()
        response = self.client.get(reverse("local_sync"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Autorización bloqueada")

    def test_retry_post_redirects_and_forwards_only_safe_fields(self):
        with patch("local_pos.sync_review.retry_conflict", return_value="accepted") as retry:
            response = self.client.post(reverse("local_sync_retry", args=[self.row.pk]),
                {"request_id":str(uuid4()), "note":"Revisé el método.", "payload":"EVIL"})
        self.assertEqual(response.status_code, 302)
        self.assertNotIn("payload", retry.call_args.kwargs)

    def test_xss_is_escaped_and_sync_errors_do_not_leak(self):
        self.row.local_result["concept"] = '<script>alert("x")</script>'
        self.row.save()
        response = self.client.get(reverse("local_sync_detail", args=[self.row.pk]))
        self.assertNotContains(response, '<script>alert("x")</script>')
        self.assertContains(response, "&lt;script&gt;")
        with patch("local_pos.sync_review.synchronize_pending", side_effect=OSError("SECRET-CONNECTION")):
            response = self.client.post(reverse("local_sync_send"), follow=True)
        self.assertNotContains(response, "SECRET-CONNECTION")
        self.assertContains(response, "Los pendientes siguen guardados")
