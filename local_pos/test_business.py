import importlib
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch
from django.conf import settings
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase, override_settings
from mainApp.test_hybrid import HybridFixture
from mainApp.models import (MetodoPago, Rol, Usuario, Inventario, Venta, Egreso, DetalleVenta, Permiso, RolPermiso,
                            OperacionHibrida, TurnoCaja, SesionHibrida, ReintegroVenta)
from pos_shared.protocol import ProtocolError
from hybrid_client.client import RemoteError
from .models import LocalNode, LocalSaleSession, LocalCommand
from .test_sales import Remote
from .replica import synchronize
from .sales import checkout, flush, refresh_authorization
from . import expenses, operations


@override_settings(HYBRID_LOCAL_SALES_ENABLED=True, HYBRID_LOCAL_OPERATIONS_ENABLED=True, HYBRID_REPLICA_MIN_INTERVAL=0)
class BusinessTests(HybridFixture, TransactionTestCase):
    databases = {"default", "replica", "replica2"}

    def setUp(self):
        super().setUp()
        MetodoPago.objects.filter(pk="efectivo").update(es_efectivo=True)
        MetodoPago.objects.create(codigo="nequi", nombre="Nequi", activo=True, aplica_4xmil_egresos=True)
        MetodoPago.objects.create(codigo="tarjeta", nombre="Tarjeta / Caja Social", activo=True)
        role = Rol.objects.using("replica").create(nombre="Web Master")
        self.actor = Usuario.objects.db_manager("replica").create_user("local", "test-only", rolid=role)
        for alias, user in (("default", self.user), ("replica", self.actor)):
            permission = Permiso.objects.using(alias).create(nombre="ventas_cambios")
            RolPermiso.objects.using(alias).create(rol_id=user.rolid_id, permiso=permission)
            from mainApp.permissions import clear_permission_cache
            clear_permission_cache(user)
        LocalNode.objects.using("replica").create(pk=settings.LOCAL_CONFIG["instance_id"])
        self.remote = Remote(self.device, self.session, self.secret)
        synchronize(self.remote, local_user_id=self.actor.pk, using="replica")
        refresh_authorization(self.remote, local_user_id=self.actor.pk, using="replica")

    def data(self, **values):
        return {"operation_id": str(uuid4()), "session_id": self.remote.session_id, **values}

    def sale(self, payments=None):
        return checkout(self.actor, self.data(items=[{"id":self.product.pk,"quantity":500}],
            cash_received="1000" if payments else "2000", expected_total="1900",
            **({"payments":payments} if payments else {})), self.remote, using="replica")

    def expense(self, **values):
        return expenses.create(self.actor, self.data(concept="  Coca-cola  ", amount_base="1000",
            method="nequi", expected_tax=True, **values), self.remote, using="replica")

    def return_request(self, sale_id, quantity=500):
        snapshot = operations.sale_snapshot(self.actor, sale_id, self.remote, using="replica")
        detail = snapshot["sale"]["items"][0]
        items = [{"detail_id":detail["detail_id"], "quantity":quantity}]
        total = operations.return_total(snapshot, items)
        return self.data(data={"sale_id":sale_id,"items":items,"expected_total":str(total),
            "refunds":{"efectivo":str(total)},"authorization":snapshot["authorization"]})

    def close_request(self, **values):
        return self.data(data={"turn_id":self.turn.pk,"cash_counted":"1900","bills_paid":"0",
                              "methods":{"nequi":"0","tarjeta":"0"},"ptm_count":0,**values})

    def test_mixed_payments_apply_only_cash_to_drawer(self):
        result = self.sale([{"medio_pago":"efectivo","monto":"900"},{"medio_pago":"nequi","monto":"1000"}])
        self.assertEqual(result["state"], "accepted")
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja, Decimal("900"))
        self.assertEqual(Venta.objects.get().mediopago, "mixto")
        self.assertIn("CAMBIO: $ 100",result["receipt"])
        self.assertIn("sin verificación",result["receipt"])

    def test_each_non_cash_method_and_mixed_work_offline(self):
        self.remote.offline = True
        for code in ("nequi","tarjeta"):
            self.assertEqual(self.sale([{"medio_pago":code,"monto":"1900"}])["state"],"pending")
        self.remote.offline = False
        flush(self.remote,using="replica")
        self.assertEqual(Venta.objects.count(),2)
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja,0)

    def test_payments_must_match_total_and_active_catalog(self):
        with self.assertRaises(ProtocolError):
            self.sale([{"medio_pago":"nequi","monto":"1000"}])
        self.assertFalse(LocalCommand.objects.using("replica").exists())
        MetodoPago.objects.filter(pk="nequi").update(activo=False)
        self.assertEqual(self.sale([{"medio_pago":"nequi","monto":"1900"}])["state"],"conflict")
        self.assertFalse(Venta.objects.exists())

    def test_expense_tax_and_author_preserved_and_does_not_touch_cash(self):
        result = self.expense()
        self.assertEqual(result["state"],"accepted")
        row = Egreso.objects.get()
        self.assertEqual(row.concepto.nombre,"COCA-COLA")
        self.assertEqual(row.monto,Decimal("1004"))
        self.assertEqual(row.impuesto_4xmil,Decimal("4"))
        self.assertEqual(row.registrado_por_id,self.user.pk)
        self.point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja,0)

    def test_expense_lost_ack_no_duplicate_and_original_tax(self):
        self.remote.offline=True
        result=self.expense()
        self.assertEqual(result["state"],"pending")
        MetodoPago.objects.filter(pk="nequi").update(aplica_4xmil_egresos=False)
        self.remote.offline=False
        original=self.remote.call
        def lost(action,data):
            response=original(action,data)
            if action=="expense": raise RemoteError("Acuse perdido")
            return response
        with patch.object(self.remote,"call",side_effect=lost): flush(self.remote,using="replica")
        self.assertEqual(Egreso.objects.count(),1)
        flush(self.remote,using="replica")
        self.assertEqual(Egreso.objects.count(),1)
        self.assertEqual(Egreso.objects.get().monto,Decimal("1004"))

    def test_return_waits_for_server_and_inventory_is_not_doubled(self):
        sale_id=self.sale()["sale_id"]
        request=self.return_request(sale_id)
        self.remote.offline=True
        result=operations.create_return(self.actor,request,self.remote,using="replica")
        self.assertEqual(result["state"],"pending")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad,-500)
        self.assertFalse(ReintegroVenta.objects.exists())
        self.remote.offline=False
        flush(self.remote,using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad,0)
        synchronize(self.remote,local_user_id=self.actor.pk,using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad,0)
        self.assertEqual(ReintegroVenta.objects.count(),1)
        self.assertEqual(operations.create_return(self.actor,request,self.remote,using="replica")["state"],"accepted")

    def test_two_return_requests_cannot_refund_same_units_twice(self):
        sale_id=self.sale()["sale_id"]
        first=self.return_request(sale_id)
        second={**first,"operation_id":str(uuid4())}
        self.remote.offline=True
        operations.create_return(self.actor,first,self.remote,using="replica")
        operations.create_return(self.actor,second,self.remote,using="replica")
        self.remote.offline=False
        flush(self.remote,using="replica")
        self.assertEqual(ReintegroVenta.objects.count(),1)
        self.assertEqual(LocalCommand.objects.using("replica").get(pk=second["operation_id"]).state,"conflict")

    def test_missing_sale_is_not_invented_offline(self):
        self.remote.offline=True
        with self.assertRaisesRegex(ProtocolError,"primera vez"):
            operations.sale_snapshot(self.actor,123,self.remote,using="replica")

    def test_close_offline_locks_local_sales_then_drains_all_kinds(self):
        self.remote.offline=True
        self.sale()
        self.expense()
        request=self.close_request()
        result=operations.create_close(self.actor,request,self.remote,using="replica")
        self.assertEqual(result["state"],"pending")
        with self.assertRaisesRegex(ProtocolError,"cierre"):
            self.sale()
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado,"ABIERTO")
        self.remote.offline=False
        flush(self.remote,using="replica")
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado,"CERRADO")
        self.assertEqual(OperacionHibrida.objects.count(),3)
        self.assertEqual(Egreso.objects.count(),1)
        self.assertTrue(LocalSaleSession.objects.using("replica").get().data["closed"])
        self.assertEqual(operations.create_close(self.actor,request,self.remote,using="replica")["state"],"accepted")

    def test_close_failure_does_not_release_or_close_cloud_turn(self):
        self.sale()
        result=operations.create_close(self.actor,self.close_request(ptm_count=1),self.remote,using="replica")
        self.assertEqual(result["state"],"conflict")
        self.turn.refresh_from_db()
        self.assertEqual(self.turn.estado,"ABIERTO")
        self.assertIsNone(SesionHibrida.objects.get(pk=self.remote.session_id).liberada_en)

    def test_migration_0048_matches_receipt_model(self):
        with override_settings(MIGRATION_MODULES={}):
            state=MigrationLoader(connection).project_state([("mainApp","0047_hybrid_replica_sale_cursor")])
        migration=importlib.import_module("mainApp.migrations.0048_hybrid_business_operations").Migration("0048_hybrid_business_operations","mainApp")
        with connection.schema_editor() as editor:
            editor.delete_model(OperacionHibrida)
            editor.create_model(state.apps.get_model("mainApp","OperacionHibrida"))
            state=migration.apply(state,editor)
        historical=state.apps.get_model("mainApp","OperacionHibrida")
        for field in OperacionHibrida._meta.fields:
            self.assertEqual(field.deconstruct()[1:],historical._meta.get_field(field.name).deconstruct()[1:])

    def test_next_session_preserves_history_without_subtracting_old_sales_again(self):
        self.sale()
        result=operations.create_close(self.actor,self.close_request(),self.remote,using="replica")
        self.assertEqual(result["state"],"accepted")
        TurnoCaja.objects.create(puntopago=self.point,cajero=self.user)
        from mainApp.services import hybrid
        new=hybrid.start_session(self.device,{"username":"hibrido","password":"testing-pass","session_id":str(uuid4())})
        from .sessions import adopt_session
        from .sales import sync_cycle
        adopt_session(new,previous_id=self.remote.session_id,requested_id=new["session_id"],using="replica")
        self.remote.session_id=new["session_id"]
        sync_cycle(self.remote,local_user_id=self.actor.pk,using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad,-500)
        self.assertEqual(LocalCommand.objects.using("replica").count(),2)
        self.assertEqual(self.sale()["state"],"accepted")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad,-1000)

    def test_lost_return_ack_then_snapshot_does_not_restore_twice(self):
        sale_id=self.sale()["sale_id"]
        request=self.return_request(sale_id)
        original=self.remote.call
        def lost(action,data):
            result=original(action,data)
            if action=="operation": raise RemoteError("Acuse perdido")
            return result
        with patch.object(self.remote,"call",side_effect=lost):
            self.assertEqual(operations.create_return(self.actor,request,self.remote,using="replica")["state"],"pending")
        synchronize(self.remote,local_user_id=self.actor.pk,using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad,0)
        flush(self.remote,using="replica")
        self.assertEqual(Inventario.objects.using("replica").get().cantidad,0)
        self.assertEqual(ReintegroVenta.objects.count(),1)

    def prepare_next_cashier(self):
        from mainApp.models import Empleado, UsuarioPermiso
        from mainApp.services import hybrid
        self.sale()
        self.assertEqual(operations.create_close(self.actor,self.close_request(),self.remote,using="replica")["state"],"accepted")
        cashier = Usuario.objects.create_user("segundo", "clave-nube-segundo", rolid=Rol.objects.create(nombre="Cajero"))
        Empleado.objects.create(usuarioid=cashier, sucursalid=self.branch, nombre="Segundo", apellido="Ficticio",
            telefono="202", email="segundo@example.test", numerodocumento="202")
        for code in ("ventas_generar", "caja_turno"):
            permission, _ = Permiso.objects.get_or_create(nombre=code)
            UsuarioPermiso.objects.create(usuario=cashier, permiso=permission, permitido=True)
        TurnoCaja.objects.create(puntopago=self.point,cajero=cashier)
        data=hybrid.start_session(self.device,{"username":"segundo","password":"clave-nube-segundo","session_id":str(uuid4())})
        return data, cashier

    def handoff(self, data):
        from .sessions import handoff_session
        return handoff_session(data,previous_id=self.remote.session_id,requested_id=data["session_id"],
            username=data["user"],local_password="clave-local-separada",using="replica")

    def test_handoff_keeps_authors_stock_and_restricts_new_cashier(self):
        from .models import LocalHandoff
        from .sales import sync_cycle
        from mainApp.permissions import user_can_change_sale, user_can_access_url_name
        data, cashier = self.prepare_next_cashier()
        previous_actor = self.actor
        following=self.handoff(data)
        self.remote.session_id=data["session_id"]
        sync_cycle(self.remote,local_user_id=following.pk,using="replica")
        previous_actor.refresh_from_db(using="replica")
        self.assertFalse(previous_actor.is_active)
        self.assertFalse(following.is_superuser or following.is_staff)
        self.assertIsNone(following.rolid_id)
        self.assertFalse(user_can_change_sale(following))
        self.assertFalse(user_can_access_url_name(following,"configuracion_funcionalidades"))
        self.assertTrue(user_can_access_url_name(following,"generar_venta"))
        self.assertTrue(following.check_password("clave-local-separada"))
        self.assertFalse(following.check_password("clave-nube-segundo"))
        with self.assertRaises(ProtocolError):
            self.sale()
        self.actor=following
        self.assertEqual(self.sale()["state"],"accepted")
        self.assertEqual(list(LocalCommand.objects.using("replica").order_by("sequence").values_list("actor_id",flat=True)),
            [previous_actor.pk, previous_actor.pk, following.pk])
        self.assertEqual(list(Venta.objects.order_by("pk").values_list("empleadoid__usuarioid_id",flat=True)),[self.user.pk,cashier.pk])
        self.assertEqual(Inventario.objects.using("replica").get().cantidad,-1000)
        self.assertEqual(LocalHandoff.objects.using("replica").count(),1)

    def test_handoff_requires_confirmed_close(self):
        from copy import deepcopy
        from .models import LocalHandoff
        data=deepcopy(LocalSaleSession.objects.using("replica").get().data)
        data.update(session_id=str(uuid4()),turn_id=self.turn.pk+1)
        with self.assertRaisesRegex(ProtocolError,"cierre"):
            self.handoff(data)
        self.assertFalse(LocalHandoff.objects.using("replica").exists())

    def test_handoff_rejects_pending_operations_and_wrong_scope(self):
        from copy import deepcopy
        data, _ = self.prepare_next_cashier()
        row=LocalCommand.objects.using("replica").first()
        row.state="pending";row.save(using="replica",update_fields=["state"])
        with self.assertRaisesRegex(ProtocolError,"pendientes"):
            self.handoff(data)
        row.state="accepted";row.save(using="replica",update_fields=["state"])
        wrong=deepcopy(data)
        wrong["point_id"]+=1;wrong["reference_scope"]["point_id"]=wrong["point_id"]
        with self.assertRaisesRegex(ProtocolError,"punto"):
            self.handoff(wrong)

    def test_handoff_retry_is_idempotent_and_blocks_until_new_copy(self):
        from .models import LocalHandoff, ReplicaState
        data, _ = self.prepare_next_cashier()
        actor=self.handoff(data)
        self.assertEqual(self.handoff(data).pk,actor.pk)
        self.assertEqual(LocalHandoff.objects.using("replica").count(),1)
        self.assertTrue(ReplicaState.objects.using("replica").get().blocked)
        with self.assertRaises(ProtocolError):
            checkout(actor,{**self.data(),"session_id":data["session_id"]},self.remote,using="replica")

    def test_handoff_audit_failure_rolls_back_accounts_and_session(self):
        from .models import LocalHandoff, LocalOperator, ReplicaState
        data, _ = self.prepare_next_cashier()
        with patch.object(LocalHandoff,"save",side_effect=RuntimeError("Disco ficticio")):
            with self.assertRaises(RuntimeError): self.handoff(data)
        self.actor.refresh_from_db(using="replica")
        self.assertTrue(self.actor.is_active)
        self.assertEqual(ReplicaState.objects.using("replica").get().local_user_id,self.actor.pk)
        self.assertEqual(str(LocalSaleSession.objects.using("replica").get().session_id),self.remote.session_id)
        self.assertFalse(LocalOperator.objects.using("replica").exists())

    def test_returning_cashier_reuses_identity_without_reusing_previous_login(self):
        from .sales import sync_cycle
        from .models import LocalOperator, LocalHandoff
        from mainApp.services import hybrid
        original_actor=self.actor
        data, _ = self.prepare_next_cashier()
        self.actor=self.handoff(data)
        self.remote.session_id=data["session_id"]
        sync_cycle(self.remote,local_user_id=self.actor.pk,using="replica")
        closed=operations.create_close(self.actor,self.data(data={"turn_id":data["turn_id"],
            "cash_counted":"0","bills_paid":"0","methods":{"nequi":"0","tarjeta":"0"},"ptm_count":0}),self.remote,using="replica")
        self.assertEqual(closed["state"],"accepted")
        TurnoCaja.objects.create(puntopago=self.point,cajero=self.user)
        next_data=hybrid.start_session(self.device,{"username":"hibrido","password":"testing-pass","session_id":str(uuid4())})
        restored=self.handoff(next_data)
        self.assertEqual(restored.pk,original_actor.pk)
        self.assertTrue(restored.is_active)
        self.assertFalse(restored.check_password("test-only"))
        self.assertEqual(LocalOperator.objects.using("replica").count(),2)
        self.assertEqual(LocalHandoff.objects.using("replica").count(),2)
