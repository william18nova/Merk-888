"""Ventas Django completas con origen y DOS receptores PostgreSQL ficticios."""
import json
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.db import connections
from django.test import TransactionTestCase, RequestFactory, override_settings
from django.utils import timezone

from hybrid_client.client import RemoteError
from mainApp import hybrid_views
from mainApp.models import (Usuario, Rol, MetodoPago, Inventario, Venta, OperacionHibrida,
                            TurnoCaja, PuntosPago, ReplicaHibrida)
from mainApp.services import hybrid
from mainApp.test_hybrid import HybridFixture
from mainApp import test_hybrid as cloud_tests
from pos_shared.protocol import ProtocolError
from .models import LocalNode, LocalCommand, LocalSaleSession, ReplicaState
from .replica import synchronize
from .sales import checkout, flush, refresh_authorization, sync_cycle, KIND


class Remote:
    url = "https://source.example.test"

    def __init__(self, device, session, secret):
        self.device_id, self.session_id, self.secret = str(device.pk), session["session_id"], secret
        self.offline = False

    def call(self, action, data):
        if self.offline:
            raise RemoteError("Corte ficticio")
        request = RequestFactory().post("/api/hybrid/v1/", content_type="application/json",
            data=json.dumps({**data, "session_id": self.session_id}),
            HTTP_AUTHORIZATION=f"Bearer {self.device_id}.{self.secret}")
        view = getattr(hybrid_views, "replica_" + action if action in {"prepare", "page"} else action.replace("-", "_"))
        response = view(request)
        result = json.loads(response.content)
        if not result.pop("ok", False):
            raise RemoteError(result.get("error"), response.status_code, result.get("code"))
        return result


@override_settings(ROOT_URLCONF="local_pos.test_replica_urls", MIDDLEWARE=[])
class CloudProtocolRegression(cloud_tests.HybridTests):
    """Las reglas previas del servidor siguen válidas en este origen aislado."""


@override_settings(HYBRID_LOCAL_SALES_ENABLED=True, HYBRID_REPLICA_MIN_INTERVAL=0)
class LocalSalesTests(HybridFixture, TransactionTestCase):
    databases = {"default", "replica", "replica2"}

    def setUp(self):
        super().setUp()
        MetodoPago.objects.filter(pk="efectivo").update(es_efectivo=True)
        self.remote = Remote(self.device, self.session, self.secret)
        self.actor = self.prepare_local("replica", self.remote)

    def prepare_local(self, alias, remote):
        role = Rol.objects.using(alias).create(nombre="Web Master")
        user = Usuario.objects.db_manager(alias).create_user("local", "test-only", rolid=role)
        LocalNode.objects.using(alias).create(pk=settings.LOCAL_CONFIG["instance_id"])
        synchronize(remote, local_user_id=user.pk, using=alias)
        refresh_authorization(remote, local_user_id=user.pk, using=alias)
        return user

    def data(self, **kwargs):
        return {"session_id": self.remote.session_id, "operation_id": str(uuid4()),
                "items": [{"id": self.product.pk, "quantity": 500}],
                "cash_received": "2000", "expected_total": "1900", **kwargs}

    def sell(self, data=None):
        return checkout(self.actor, data or self.data(), self.remote, using="replica")

    def test_cloud_first_and_negative_projection_then_replica_does_not_double_subtract(self):
        seen = []
        original = self.remote.call
        def call(action, payload):
            if action == "sale":
                seen.append(Inventario.objects.using("replica").get().cantidad)
                self.assertTrue(LocalCommand.objects.using("replica").filter(state="intent").exists())
            return original(action, payload)
        with patch.object(self.remote, "call", side_effect=call):
            result = self.sell()
        self.assertEqual(seen, [0])
        self.assertEqual(result["state"], "accepted")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -500)
        synchronize(self.remote, local_user_id=self.actor.pk, using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -500)
        self.assertEqual(ReplicaState.objects.using("replica").get().sale_cursor["sequence"], 1)

    def test_offline_two_sales_survive_new_service_instance_and_reconnect_once(self):
        self.remote.offline = True
        one, two = self.data(), self.data()
        self.assertEqual(self.sell(one)["state"], "pending")
        self.assertEqual(self.sell(two)["state"], "pending")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -1000)
        self.assertEqual(Venta.objects.count(), 0)
        connections["replica"].close()
        restarted = Remote(self.device, self.session, self.secret)
        flush(restarted, using="replica")
        flush(restarted, using="replica")
        self.assertEqual(Venta.objects.count(), 2)
        self.assertEqual(OperacionHibrida.objects.count(), 2)
        synchronize(restarted, local_user_id=self.actor.pk, using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -1000)

    def test_lost_cloud_response_keeps_same_uuid_without_duplicate(self):
        original = self.remote.call
        data = self.data()
        def lost(action, payload):
            result = original(action, payload)
            if action == "sale":
                raise RemoteError("Respuesta perdida")
            return result
        with patch.object(self.remote, "call", side_effect=lost):
            self.assertEqual(self.sell(data)["state"], "pending")
        self.assertEqual(Venta.objects.count(), 1)
        # Incluso si entra primero el inventario remoto, no restar otra vez.
        synchronize(self.remote, local_user_id=self.actor.pk, using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -500)
        self.assertEqual(self.sell(data)["state"], "accepted")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -500)

    def test_crash_after_intent_before_network_recovers(self):
        with patch("local_pos.sales.flush", side_effect=RuntimeError("Reinicio ficticio")), self.assertRaises(RuntimeError):
            self.sell()
        self.assertEqual(LocalCommand.objects.using("replica").get().state, "intent")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, 0)
        flush(self.remote, using="replica")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -500)

    def test_crash_after_remote_commit_rolls_back_projection_but_retries_same_sale(self):
        save = LocalCommand.save
        def fail(command, *args, **kwargs):
            if command.state == "accepted":
                raise RuntimeError("Disco interrumpido")
            return save(command, *args, **kwargs)
        with patch.object(LocalCommand, "save", fail), self.assertRaises(RuntimeError):
            self.sell()
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, 0)
        flush(self.remote, using="replica")
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -500)

    def test_idempotent_retry_cannot_change_quantities(self):
        data = self.data()
        self.sell(data)
        self.assertEqual(self.sell(data)["state"], "accepted")
        with self.assertRaises(ProtocolError):
            self.sell({**data, "cash_received": "3000"})
        self.assertEqual(Venta.objects.count(), 1)

    def test_expired_permission_does_not_create_sale(self):
        ReplicaState.objects.using("replica").update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(ProtocolError):
            self.sell()
        self.assertFalse(LocalCommand.objects.using("replica").exists())

    def test_background_failure_updates_connection_state(self):
        self.remote.offline = True
        with self.assertRaises(RemoteError):
            sync_cycle(self.remote, local_user_id=self.actor.pk, using="replica")
        self.assertFalse(LocalSaleSession.objects.using("replica").get().online)

    def test_revocation_is_not_treated_as_offline(self):
        self.device.activo = False
        self.device.save(update_fields=["activo"])
        result = self.sell()
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, 0)
        self.assertFalse(Venta.objects.exists())
        self.assertTrue(ReplicaState.objects.using("replica").get().blocked)

    def test_wrong_total_is_rejected_before_intent(self):
        with self.assertRaises(ProtocolError):
            self.sell(self.data(expected_total="1"))
        self.assertFalse(LocalCommand.objects.using("replica").exists())

    def test_two_tabs_concurrent_retry_only_one_sale(self):
        data = self.data()
        def submit():
            try:
                actor = Usuario.objects.using("replica").select_related("rolid").get(pk=self.actor.pk)
                return checkout(actor, data, self.remote, using="replica")
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda _: submit(), range(2)))
        self.assertTrue(all(row["state"] == "accepted" for row in result))
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(LocalCommand.objects.using("replica").count(), 1)

    def test_two_computers_offline_then_combined_inventory_without_overwriting(self):
        second_point = PuntosPago.objects.create(nombre="CAJA 2", sucursalid=self.branch)
        TurnoCaja.objects.create(puntopago=second_point, cajero=self.user)
        device, code = hybrid.create_device(actor=self.user, point=second_point, name="PC DOS")
        secret = "b"*64
        hybrid.enroll({"code": code, "secret": secret})
        device.refresh_from_db()
        session = hybrid.start_session(device, {"session_id": str(uuid4()), "username": "hibrido", "password": "testing-pass"})
        second = Remote(device, session, secret)
        actor = self.prepare_local("replica2", second)
        self.remote.offline = second.offline = True
        self.sell()
        checkout(actor, self.data(session_id=second.session_id), second, using="replica2")
        self.remote.offline = False
        flush(self.remote, using="replica")
        second.offline = False
        # Descargar la venta del PC1 antes de subir la del PC2: conservar la
        # resta local aún pendiente, además de la ya confirmada del PC1.
        synchronize(second, local_user_id=actor.pk, using="replica2")
        self.assertEqual(Inventario.objects.using("replica2").get().cantidad, -1000)
        flush(second, using="replica2")
        for remote, user, alias in ((self.remote, self.actor, "replica"), (second, actor, "replica2")):
            synchronize(remote, local_user_id=user.pk, using=alias)
            self.assertEqual(Inventario.objects.using(alias).get().cantidad, -1000)
        self.assertEqual(Inventario.objects.get().cantidad, -1000)
        self.assertEqual(Venta.objects.count(), 2)

    def test_replica_without_sale_cursor_cannot_replace_projected_inventory(self):
        self.remote.offline = True
        self.sell()
        self.remote.offline = False
        original = self.remote.call
        def old_server(action, data):
            result = original(action, data)
            if action == "prepare":
                result.pop("sale_cursor", None)
            return result
        with patch.object(self.remote, "call", side_effect=old_server), self.assertRaisesRegex(ValueError, "qué ventas"):
            synchronize(self.remote, local_user_id=self.actor.pk, using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -500)


@override_settings(HYBRID_LOCAL_SALES_ENABLED=True, ALLOWED_HOSTS=["testserver", "127.0.0.1"])
class LocalSaleEndpointTests(TransactionTestCase):
    def setUp(self):
        from scripts.full_local_lab import seed
        from django.core.cache import cache
        cache.clear()
        seed()
        self.actor = Usuario.objects.get(nombreusuario="laboratorio")
        self.node = LocalNode.objects.get()
        self.state = ReplicaState.objects.create(node=self.node, local_user=self.actor,
            server="https://source.example.test", device_id=uuid4(), active_id=uuid4(),
            expires_at=timezone.now()+timedelta(hours=1))
        LocalSaleSession.objects.create(node=self.node, session_id=uuid4(), data={}, products={})
        self.client.force_login(self.actor)

    def test_anonymous_cannot_read_operations(self):
        self.client.logout()
        self.assertEqual(self.client.get("/local/sales/api/history").status_code, 401)

    def test_different_local_user_cannot_read_or_write_this_session(self):
        other = Usuario.objects.create_user("otro", "test-only", rolid=self.actor.rolid)
        self.client.force_login(other)
        self.assertEqual(self.client.get("/local/sales/api/history").status_code, 409)
        self.assertEqual(self.client.post("/local/sales/api/checkout", {}, content_type="application/json").status_code, 409)

    def test_csrf_required_for_checkout(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        with patch("local_pos.sale_views.remote") as network:
            self.assertEqual(client.post("/local/sales/api/checkout", {}, content_type="application/json").status_code, 403)
            network.assert_not_called()

    def test_checkout_requires_post_and_simulator_is_disabled_by_default(self):
        self.assertEqual(self.client.get("/local/sales/api/checkout").status_code, 405)
        self.assertEqual(self.client.post("/local/sales/api/test-network", {}, content_type="application/json").status_code, 405)

    def test_original_write_endpoints_remain_blocked(self):
        self.assertEqual(self.client.post("/agregar_producto/", {}).status_code, 409)
