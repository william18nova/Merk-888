"""Bloqueos reales: estas pruebas se omiten en SQLite, no simulan PostgreSQL."""
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4
from unittest import skipUnless

from django.db import connection, connections, close_old_connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from .models import (Empleado, EquipoHibrido, Inventario, OperacionHibrida,
                     PuntosPago, SesionHibrida, TurnoCaja, Usuario, Venta)
from .services import hybrid
from .services import hybrid_recovery as recovery
from .test_hybrid import HybridFixture
from .views import GenerarVentaView


@skipUnless(connection.vendor == "postgresql", "Requiere PostgreSQL local desechable; SQLite no verifica bloqueos.")
@override_settings(SECRET_KEY="hybrid-test-key-no-production")
class HybridPostgresConcurrencyTests(HybridFixture, TransactionTestCase):
    def parallel(self, *jobs):
        barrier = threading.Barrier(len(jobs), timeout=10)
        def run(job):
            close_old_connections()
            try:
                barrier.wait()
                return job()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = [pool.submit(run, job) for job in jobs]
            return [f.result(timeout=25) for f in futures]

    def send(self, data, device_id=None):
        return hybrid.accept_sale(EquipoHibrido.objects.get(pk=device_id or self.device.pk), data)

    def test_same_uuid_at_same_time_is_one_sale(self):
        data = self.payload()
        first, second = self.parallel(lambda:self.send(copy.deepcopy(data)), lambda:self.send(copy.deepcopy(data)))
        self.assertEqual(first,second)
        self.assertEqual(Venta.objects.count(),1)
        self.assertEqual(OperacionHibrida.objects.count(),1)
        self.stock.refresh_from_db();self.assertEqual(self.stock.cantidad,-500)

    def test_different_uuids_cannot_claim_the_same_sequence(self):
        def send(data):
            try:
                self.send(data)
                return "accepted"
            except hybrid.HybridError as error:
                return error.code
        results=self.parallel(lambda:send(self.payload()),lambda:send(self.payload()))
        self.assertCountEqual(results,["accepted","sequence"])
        self.assertEqual(Venta.objects.count(),1)

    def test_different_points_and_web_sale_do_not_overwrite_inventory(self):
        point=PuntosPago.objects.create(nombre="Caja 2",sucursalid=self.branch)
        user=Usuario.objects.create_user("otro-pg","test-pass",rolid=self.user.rolid)
        Empleado.objects.create(usuarioid=user,sucursalid=self.branch,nombre="Otro",apellido="Cajero",telefono="102",email="pg@example.test",numerodocumento="124")
        turn=TurnoCaja.objects.create(puntopago=point,cajero=user)
        device,code=hybrid.create_device(actor=self.user,point=point,name="PC 2")
        hybrid.enroll({"code":code,"secret":"b"*64})
        session=hybrid.start_session(device,{"username":"otro-pg","password":"test-pass","session_id":str(uuid4())})
        quote=hybrid.catalog(device,{"session_id":session["session_id"]})["updates"][0]["quote"]
        second=self.payload(session_id=session["session_id"],items=[{"id":self.product.pk,"quantity":500,"quote":quote}])
        def web_sale():
            import json
            response=GenerarVentaView._crear_venta_ultra_fast(
                Usuario.objects.get(pk=self.user.pk),self.branch,self.point,None,
                [{"medio_pago":"efectivo","monto":Decimal("1900")}],
                [{"productoid":self.product.pk,"producto":"TOMATE","cantidad":500,"precio_unitario":Decimal("3.80"),"subtotal":Decimal("1900")}],
                Decimal("1900"),Decimal("2000"),turno=self.turn.pk,
            )
            result=json.loads(response.content)
            if not result.get("success"):raise AssertionError(result.get("error"))
            return result
        self.parallel(lambda:self.send(self.payload()),lambda:self.send(second,device.pk),web_sale)
        self.assertEqual(Venta.objects.count(),3)
        self.stock.refresh_from_db();self.assertEqual(self.stock.cantidad,-1500)
        self.point.refresh_from_db();point.refresh_from_db()
        self.assertEqual(self.point.dinerocaja,Decimal("3800"))
        self.assertEqual(point.dinerocaja,Decimal("1900"))

    def test_closing_turn_in_parallel_cannot_drop_pending_session(self):
        def close():
            from django.core.exceptions import ValidationError
            with transaction.atomic():
                turn=TurnoCaja.objects.select_for_update().get(pk=self.turn.pk)
                turn.estado="CIERRE"
                try:turn.save()
                except ValidationError:return "blocked"
                return "closed"
        sale, closed=self.parallel(lambda:self.send(self.payload()),close)
        self.assertEqual(closed,"blocked")
        self.assertEqual(sale["status"],"accepted")
        self.turn.refresh_from_db();self.assertEqual(self.turn.estado,"ABIERTO")

    def test_release_cannot_skip_a_simultaneous_unsent_sale(self):
        def release():
            try:
                hybrid.release_session(self.device,{"session_id":self.session["session_id"],"sequence":1})
                return "released"
            except hybrid.HybridError:
                return "pending"
        sale,released=self.parallel(lambda:self.send(self.payload()),release)
        self.assertEqual(sale["status"],"accepted")
        self.assertIn(released,{"released","pending"})
        self.assertEqual(SesionHibrida.objects.get().secuencia,1)

    def test_two_recovery_finishes_apply_exactly_once(self):
        payload=self.payload()
        record,code=recovery.authorize(actor=self.user,device_id=self.device.pk,reason="Prueba de recuperación paralela",isolated=True)
        bundle={"device_id":str(self.device.pk),"backup_created_at":timezone.now().isoformat(),"operations":[{"state":"pending","payload":payload}]}
        recovery.prepare({"code":code,"secret":"b"*64,"manifest":bundle})
        recovery.approve(actor=self.user,recovery_id=record.pk,understood=True)
        data={"recovery_id":str(record.pk),"secret":"b"*64,"manifest":bundle}
        first,second=self.parallel(lambda:recovery.finish(copy.deepcopy(data)),lambda:recovery.finish(copy.deepcopy(data)))
        self.assertEqual(first,second)
        self.assertEqual(Venta.objects.count(),1)
        self.stock.refresh_from_db(); self.point.refresh_from_db()
        self.assertEqual(self.stock.cantidad,-500)
        self.assertEqual(self.point.dinerocaja,Decimal("1900"))

    def test_revocation_serializes_with_already_authenticated_request(self):
        stale=hybrid.authenticate_device(f"Bearer {self.device.pk}.{self.secret}")
        payload=self.payload()
        def send():
            try:
                hybrid.accept_sale(stale,payload)
                return "accepted"
            except hybrid.HybridError as exc:return exc.code
        _,status=self.parallel(lambda:recovery.authorize(actor=self.user,device_id=self.device.pk,reason="Equipo retirado para pruebas",isolated=True),send)
        self.assertIn(status,{"accepted","authentication"})
        self.assertEqual(Venta.objects.count(),int(status=="accepted"))
        with self.assertRaises(hybrid.HybridError):hybrid.accept_sale(stale,payload)
        self.device.refresh_from_db();self.assertEqual(self.device.token_hash,"")

    def test_turn_cannot_close_before_pending_recovery_commits(self):
        from django.core.exceptions import ValidationError
        payload=self.payload()
        record,code=recovery.authorize(actor=self.user,device_id=self.device.pk,reason="Prueba cierre y recuperación",isolated=True)
        bundle={"device_id":str(self.device.pk),"backup_created_at":timezone.now().isoformat(),"operations":[{"state":"pending","payload":payload}]}
        recovery.prepare({"code":code,"secret":"b"*64,"manifest":bundle})
        recovery.approve(actor=self.user,recovery_id=record.pk,understood=True)
        def close():
            with transaction.atomic():
                turn=TurnoCaja.objects.select_for_update().get(pk=self.turn.pk)
                turn.estado="CIERRE"
                try:turn.save()
                except ValidationError:return "blocked"
                return "closed"
        result,status=self.parallel(lambda:recovery.finish({"recovery_id":str(record.pk),"secret":"b"*64,"manifest":bundle}),close)
        self.assertEqual(result["state"],"completed")
        self.assertIn(status,{"blocked","closed"})
        self.assertEqual(Venta.objects.count(),1)
        self.assertFalse(SesionHibrida.objects.filter(liberada_en__isnull=True).exists())
