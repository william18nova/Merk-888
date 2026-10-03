"""Dos PostgreSQL aislados: origen ficticio y réplica; nunca bases reales."""
import copy
import importlib
import json
import threading
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4
from wsgiref.simple_server import make_server, WSGIRequestHandler

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.db import connections
from django.db.migrations.loader import MigrationLoader
from django.http import HttpResponse
from django.test import RequestFactory, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from hybrid_client.client import RemoteError
from mainApp import hybrid_views
from mainApp.models import (Categoria, Cliente, Inventario, MetodoPago, Producto, PuntosPago,
                            ReplicaHibrida, SesionHibrida, Sucursal, Usuario)
from mainApp.test_hybrid import HybridFixture
from pos_shared.replica import manifest
from .models import LocalNode, LocalCommand, ReplicaState, ReplicaTransfer, ReplicaPage
from .replica import synchronize, ReplicaError
from .replica_transport import ReplicaRemote


class TestRemote:
    url = "https://source.example.test"

    def __init__(self, fixture):
        self.device_id = str(fixture.device.pk)
        self.session_id = fixture.session["session_id"]
        self.secret = fixture.secret
        self.calls = []

    def call(self, action, data):
        self.calls.append((action, copy.deepcopy(data)))
        request = RequestFactory().post("/api/hybrid/v1/replica/" + action + "/",
            data=json.dumps({**data, "session_id": self.session_id}), content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.device_id}.{self.secret}")
        response = getattr(hybrid_views, "replica_" + action)(request)
        payload = json.loads(response.content)
        if not payload.pop("ok", False):
            raise RemoteError(payload.get("error"), response.status_code, payload.get("code"))
        return payload


@override_settings(HYBRID_REPLICA_MIN_INTERVAL=0, ALLOWED_HOSTS=["testserver", "127.0.0.1"])
class ReferenceReplicaTests(HybridFixture, TransactionTestCase):
    databases = {"default", "replica"}

    def setUp(self):
        super().setUp()
        self.local_user = Usuario.objects.db_manager("replica").create_user("local", "only-test-password")
        self.node = LocalNode.objects.using("replica").create(id=settings.LOCAL_CONFIG["instance_id"])
        self.remote = TestRemote(self)
        self.method = MetodoPago.objects.get(codigo="efectivo")
        self.method.es_efectivo = True
        self.method.save()

    def sync(self, remote=None):
        return synchronize(remote or self.remote, local_user_id=self.local_user.pk, using="replica")

    def test_initial_download_keeps_negative_stock_and_excludes_secrets(self):
        Inventario.objects.filter(pk=self.stock.pk).update(cantidad=-812)
        MetodoPago.objects.create(codigo="nequi", nombre="Nequi", aplica_4xmil_egresos=True)
        state = self.sync()
        self.assertEqual(Inventario.objects.using("replica").get(pk=self.stock.pk).cantidad, -812)
        self.assertEqual(Producto.objects.using("replica").get(pk=self.product.pk).precio, Decimal("3.80"))
        self.assertTrue(MetodoPago.objects.using("replica").get(pk="nequi").aplica_4xmil_egresos)
        self.assertEqual(Usuario.objects.using("replica").count(), 1)
        self.assertFalse(Usuario.objects.using("replica").filter(nombreusuario=self.user.nombreusuario).exists())
        serialized = json.dumps(ReplicaHibrida.objects.get(pk=state.active_id).filas)
        for secret in ("password", "token_hash", "contrase", "dinerocaja", "telefono", "email"):
            self.assertNotIn(secret, serialized)
        self.assertEqual(state.scope["branch_id"], self.branch.pk)

    def test_only_assigned_branch_and_point_are_downloaded(self):
        other = Sucursal.objects.create(nombre="NO AUTORIZADA")
        PuntosPago.objects.create(nombre="CAJA AJENA", sucursalid=other, dinerocaja=987654)
        Inventario.objects.create(sucursalid=other, productoid=self.product, cantidad=98765)
        self.sync()
        self.assertEqual(list(Sucursal.objects.using("replica").values_list("pk", flat=True)), [self.branch.pk])
        self.assertEqual(list(PuntosPago.objects.using("replica").values_list("pk", flat=True)), [self.point.pk])
        self.assertEqual(Inventario.objects.using("replica").count(), 1)

    def test_bulk_changes_send_only_changed_rows_and_noop_sends_none(self):
        self.sync()
        Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("5.25"))
        Inventario.objects.filter(pk=self.stock.pk).update(cantidad=-1900)
        state = self.sync()
        self.assertEqual(len(ReplicaHibrida.objects.get(pk=state.active_id).cambios), 2)
        self.assertEqual(Inventario.objects.using("replica").get().cantidad, -1900)
        self.assertEqual(Producto.objects.using("replica").get().precio, Decimal("5.25"))
        state = self.sync()
        self.assertEqual(ReplicaHibrida.objects.get(pk=state.active_id).cambios, [])

    def test_refresh_only_writes_changed_business_rows(self):
        self.sync()
        from .replica import MODELS
        tables = {model._meta.db_table for model in MODELS.values()} | {"local_pos_replicarow"}

        def written_tables(queries):
            return {table for table in tables for query in queries
                    if any(query["sql"].startswith(f'{verb} "{table}"')
                           for verb in ("INSERT INTO", "UPDATE", "DELETE FROM"))}

        with CaptureQueriesContext(connections["replica"]) as unchanged:
            self.sync()
        self.assertEqual(written_tables(unchanged), set())
        Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("9"))
        with CaptureQueriesContext(connections["replica"]) as changed:
            self.sync()
        self.assertEqual(written_tables(changed), {"productos", "local_pos_replicarow"})

    def test_switching_cash_method_keeps_unique_constraint(self):
        MetodoPago.objects.create(codigo="nuevo", nombre="OTRO EFECTIVO")
        self.sync()
        MetodoPago.objects.filter(pk="efectivo").update(es_efectivo=False)
        MetodoPago.objects.filter(pk="nuevo").update(es_efectivo=True)
        self.sync()
        self.assertEqual(list(MetodoPago.objects.using("replica").filter(es_efectivo=True)
                              .values_list("pk", flat=True)), ["nuevo"])

    def test_payment_method_with_text_history_cannot_be_deleted(self):
        self.sync()
        from mainApp.models import ConceptoEgreso, Egreso
        concept = ConceptoEgreso.objects.using("replica").create(nombre="PAGO LOCAL")
        Egreso.objects.using("replica").create(concepto=concept, monto=1,
            medio_pago="efectivo", registrado_por=self.local_user, registrado_por_nombre="PRUEBA")
        MetodoPago.objects.filter(pk="efectivo").delete()
        with self.assertRaisesRegex(ReplicaError, "historial"):
            self.sync()
        self.assertTrue(MetodoPago.objects.using("replica").filter(pk="efectivo").exists())
        self.assertEqual(Egreso.objects.using("replica").count(), 1)

    def test_deletions_and_creations_applied_together(self):
        self.sync()
        self.product.delete()
        new = Producto.objects.create(nombre="NUEVO", categoria=Categoria.objects.first(), precio=Decimal("4"))
        self.sync()
        self.assertEqual(list(Producto.objects.using("replica").values_list("pk", flat=True)), [new.pk])
        self.assertFalse(Inventario.objects.using("replica").exists())

    def test_pending_local_operations_prevent_overwrite(self):
        initial = self.sync()
        LocalCommand.objects.using("replica").create(operation_id=uuid4(), node=self.node,
            sequence=1, actor=self.local_user, kind="test", payload={}, fingerprint="a"*64)
        Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("9"))
        with self.assertRaisesRegex(ReplicaError, "pendientes"):
            self.sync()
        state = ReplicaState.objects.using("replica").get()
        self.assertEqual(state.active_id, initial.active_id)
        self.assertEqual(Producto.objects.using("replica").get().precio, Decimal("3.80"))

    def test_untracked_local_data_never_overwritten(self):
        Categoria.objects.using("replica").create(nombre="ESTA INFORMACION NO SE BORRA")
        with self.assertRaisesRegex(ReplicaError, "fuera de la réplica"):
            self.sync()
        self.assertEqual(Categoria.objects.using("replica").get().nombre, "ESTA INFORMACION NO SE BORRA")
        self.assertFalse(Producto.objects.using("replica").exists())

    def test_local_edits_are_not_silently_overwritten(self):
        self.sync()
        Producto.objects.using("replica").filter(pk=self.product.pk).update(precio=Decimal("7"))
        with self.assertRaisesRegex(ReplicaError, "fuera de la réplica"):
            self.sync()
        self.assertEqual(Producto.objects.using("replica").get().precio, Decimal("7"))

    def test_initial_interruption_exposes_no_partial_data_and_resumes(self):
        category = Categoria.objects.first()
        Producto.objects.bulk_create([Producto(nombre=f"PRODUCTO {i}", categoria=category, precio=1) for i in range(205)])
        real_call = self.remote.call
        def disconnected(action, data):
            if action == "page" and data["offset"] >= 200:
                raise RemoteError("simulated offline")
            return real_call(action, data)
        with patch.object(self.remote, "call", side_effect=disconnected), self.assertRaises(RemoteError):
            self.sync()
        self.assertFalse(Producto.objects.using("replica").exists())
        self.assertEqual(ReplicaPage.objects.using("replica").count(), 1)
        self.remote.calls.clear()
        state = self.sync()
        self.assertTrue(state.active_id)
        self.assertEqual(Producto.objects.using("replica").count(), 206)
        offsets = [data["offset"] for action, data in self.remote.calls if action == "page"]
        self.assertEqual(offsets, [200])

    def test_lost_prepare_response_reuses_same_snapshot(self):
        real_call = self.remote.call
        def lost(action, data):
            result = real_call(action, data)
            if action == "prepare":
                raise RemoteError("simulated lost response")
            return result
        with patch.object(self.remote, "call", side_effect=lost), self.assertRaises(RemoteError):
            self.sync()
        first = ReplicaHibrida.objects.get().pk
        self.assertEqual(self.sync().active_id, first)
        self.assertEqual(ReplicaHibrida.objects.count(), 1)

    def test_cloud_changes_during_download_wait_for_next_snapshot(self):
        real_call = self.remote.call
        def concurrent_change(action, data):
            result = real_call(action, data)
            if action == "prepare":
                Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("6"))
            return result
        with patch.object(self.remote, "call", side_effect=concurrent_change):
            self.sync()
        self.assertEqual(Producto.objects.using("replica").get().precio, Decimal("3.80"))
        self.sync()
        self.assertEqual(Producto.objects.using("replica").get().precio, Decimal("6"))

    def test_corrupt_page_preserves_previous_complete_copy(self):
        initial = self.sync()
        Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("6"))
        real_call = self.remote.call
        def corrupt(action, data):
            result = real_call(action, data)
            if action == "page":
                result["changes"][0]["values"]["precio"] = "12345"
            return result
        with patch.object(self.remote, "call", side_effect=corrupt), self.assertRaises(ReplicaError):
            self.sync()
        self.assertEqual(ReplicaState.objects.using("replica").get().active_id, initial.active_id)
        self.assertEqual(Producto.objects.using("replica").get().precio, Decimal("3.80"))

    def test_failure_during_activation_rolls_back_all_models(self):
        from . import replica
        with patch.object(replica.ReplicaRow.objects, "bulk_create", side_effect=RuntimeError("unused")):
            # Usar la clase QuerySet cubre la conexión explícita .using().
            from django.db.models.query import QuerySet
            original = QuerySet.bulk_create
            def explode(query, *args, **kwargs):
                if query.model is replica.ReplicaRow:
                    raise RuntimeError("simulated disk failure")
                return original(query, *args, **kwargs)
            with patch.object(QuerySet, "bulk_create", explode), self.assertRaises(RuntimeError):
                self.sync()
        self.assertFalse(Producto.objects.using("replica").exists())
        self.assertFalse(Sucursal.objects.using("replica").exists())
        self.assertIsNone(ReplicaState.objects.using("replica").get().active_id)
        self.sync()
        self.assertTrue(Producto.objects.using("replica").exists())

    def test_revoked_device_blocks_refresh_and_local_access(self):
        self.sync()
        self.device.activo = False
        self.device.save(update_fields=["activo"])
        with self.assertRaises(RemoteError) as caught:
            self.sync()
        self.assertEqual(caught.exception.status, 403)
        self.assertTrue(ReplicaState.objects.using("replica").get().blocked)
        self.assertTrue(Producto.objects.using("replica").exists())

    def test_expired_session_cannot_download(self):
        SesionHibrida.objects.filter(pk=self.session["session_id"]).update(vence_en=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(RemoteError) as caught:
            self.sync()
        self.assertEqual(caught.exception.status, 403)
        self.assertFalse(Producto.objects.using("replica").exists())

    def test_inactive_cloud_user_cannot_refresh_cached_permission(self):
        self.sync()
        Usuario.objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(RemoteError) as caught:
            self.sync()
        self.assertEqual(caught.exception.status, 403)
        self.assertTrue(ReplicaState.objects.using("replica").get().blocked)

    def test_changed_scope_blocks_old_authorization_immediately(self):
        self.sync()
        from mainApp.services import hybrid_replica
        original = hybrid_replica.authorize
        def changed(*args, **kwargs):
            device, session, scope = original(*args, **kwargs)
            scope["routes"] = [r for r in scope["routes"] if r != "visualizar_clientes"]
            return device, session, scope
        with patch.object(hybrid_replica, "authorize", changed), self.assertRaises(ReplicaError):
            self.sync()
        self.assertTrue(ReplicaState.objects.using("replica").get().blocked)

    def test_expired_partial_download_can_restart_without_losing_previous_data(self):
        real_call = self.remote.call
        def interrupted(action, data):
            if action == "page":
                raise RemoteError("simulated offline")
            return real_call(action, data)
        with patch.object(self.remote, "call", side_effect=interrupted), self.assertRaises(RemoteError):
            self.sync()
        ReplicaHibrida.objects.all().delete()
        with self.assertRaises(RemoteError) as caught:
            self.sync()
        self.assertEqual(caught.exception.code, "replica_expired")
        self.assertTrue(ReplicaTransfer.objects.using("replica").get().abandoned)
        self.sync()
        self.assertEqual(Producto.objects.using("replica").count(), 1)

    def test_deletion_with_local_history_does_not_cascade(self):
        self.sync()
        from mainApp.models import PreciosProveedor, Proveedor
        provider = Proveedor.objects.using("replica").create(nombre="LOCAL HISTORY")
        PreciosProveedor.objects.using("replica").create(productoid_id=self.product.pk, proveedorid=provider, precio=10)
        self.product.delete()
        with self.assertRaisesRegex(ReplicaError, "historial"):
            self.sync()
        self.assertTrue(Producto.objects.using("replica").exists())
        self.assertTrue(PreciosProveedor.objects.using("replica").exists())

    def test_arbitrary_fields_never_reach_django_models(self):
        from .replica import validate_rows
        description = self.remote.call("prepare", {"request_id": str(uuid4()), "base_id": None})
        rows = ReplicaHibrida.objects.get(pk=description["snapshot_id"]).filas
        rows[0]["values"]["password"] = "never-import"
        with self.assertRaisesRegex(ReplicaError, "campos"):
            validate_rows(rows, description["scope"])

    def test_same_request_id_cannot_change_its_base(self):
        request_id = str(uuid4())
        self.remote.call("prepare", {"request_id": request_id, "base_id": None})
        with self.assertRaises(RemoteError) as caught:
            self.remote.call("prepare", {"request_id": request_id, "base_id": str(uuid4())})
        self.assertEqual(caught.exception.status, 403)

    def test_snapshot_cannot_be_read_with_another_device_or_token(self):
        desc = self.remote.call("prepare", {"request_id": str(uuid4()), "base_id": None})
        self.remote.secret = "f"*64
        with self.assertRaises(RemoteError) as caught:
            self.remote.call("page", {"snapshot_id": desc["snapshot_id"], "offset": 0})
        self.assertEqual(caught.exception.status, 401)

    def test_removed_base_falls_back_to_complete_snapshot(self):
        first = self.sync()
        ReplicaHibrida.objects.filter(pk=first.active_id).delete()
        Producto.objects.filter(pk=self.product.pk).update(precio=Decimal("8"))
        state = self.sync()
        self.assertIsNone(ReplicaHibrida.objects.get(pk=state.active_id).base_id)
        self.assertEqual(Producto.objects.using("replica").get().precio, Decimal("8"))

    def test_method_deactivation_and_client_edit_replicate(self):
        self.sync()
        MetodoPago.objects.filter(pk="efectivo").update(activo=False)
        client = Cliente.objects.first()
        Cliente.objects.filter(pk=client.pk).update(nombre="NOMBRE ACTUALIZADO")
        self.sync()
        self.assertFalse(MetodoPago.objects.using("replica").get().activo)
        self.assertEqual(Cliente.objects.using("replica").get(pk=client.pk).nombre, "NOMBRE ACTUALIZADO")

    @override_settings(ROOT_URLCONF="local_pos.test_replica_urls", MIDDLEWARE=[])
    def test_real_http_download_into_second_database(self):
        from django.core.wsgi import get_wsgi_application
        class Quiet(WSGIRequestHandler):
            def log_message(self, *args):
                pass
        server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=Quiet)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            remote = ReplicaRemote({"url": f"http://127.0.0.1:{server.server_port}",
                "device_id": str(self.device.pk), "secret": self.secret,
                "session_id": self.session["session_id"]}, allow_local=True)
            state = self.sync(remote)
            self.assertTrue(state.active_id)
            self.assertEqual(Producto.objects.using("replica").get().nombre, self.product.nombre)
        finally:
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

    def test_replica_migrations_match_model(self):
        self.assertEqual(connection.settings_dict["HOST"], "127.0.0.1")
        self.assertEqual(connection.settings_dict["NAME"], "test_nova_full_local_lab")
        with override_settings(MIGRATION_MODULES={}):
            state = MigrationLoader(connection).project_state([("mainApp", "0045_hybrid_recovery")])
        migration = importlib.import_module("mainApp.migrations.0046_hybrid_reference_replica").Migration("0046_hybrid_reference_replica", "mainApp")
        cursor_migration = importlib.import_module("mainApp.migrations.0047_hybrid_replica_sale_cursor").Migration("0047_hybrid_replica_sale_cursor", "mainApp")
        with connection.schema_editor() as editor:
            editor.delete_model(ReplicaHibrida)
            state = migration.apply(state, editor)
            state = cursor_migration.apply(state, editor)
        historical = state.apps.get_model("mainApp", "ReplicaHibrida")
        for field in ReplicaHibrida._meta.fields:
            self.assertEqual(field.deconstruct()[1:], historical._meta.get_field(field.name).deconstruct()[1:])
