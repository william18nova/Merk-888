import json
import tempfile
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from hybrid_client.client import Client as LocalClient, RemoteError
from hybrid_client.store import Store
from .models import (Categoria, ConfiguracionFuncionalidad, Empleado, EquipoHibrido, Inventario,
    MetodoPago, OperacionHibrida, Producto, PuntosPago, Rol, SesionHibrida, Sucursal, TurnoCaja, Usuario, Venta)
from .services import hybrid
from .services.feature_flags import HYBRID_POS_FEATURE, TURN_REQUIRED_FEATURE, clear_feature_cache
from .views import TurnoCajaIniciarCierreApi, TurnoCajaIniciarCierreAPI


class HybridFixture:
    def setUp(self):
        cache.clear()
        self.branch = Sucursal.objects.create(nombre="Pruebas Híbrido")
        self.point = PuntosPago.objects.create(nombre="Caja 1", sucursalid=self.branch)
        role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("hibrido", "testing-pass", rolid=role)
        Empleado.objects.create(usuarioid=self.user, sucursalid=self.branch, nombre="Cajero", apellido="Prueba", telefono="101", email="test@example.test", numerodocumento="123")
        self.turn = TurnoCaja.objects.create(puntopago=self.point, cajero=self.user)
        MetodoPago.objects.create(codigo="efectivo", nombre="Efectivo", activo=True)
        ConfiguracionFuncionalidad.objects.create(clave=HYBRID_POS_FEATURE, habilitada=True)
        ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=True)
        clear_feature_cache()
        cat = Categoria.objects.create(nombre="Prueba")
        self.product = Producto.objects.create(nombre="TOMATE", precio=Decimal("3.80"), categoria=cat, codigo_de_barras="770123")
        self.stock = Inventario.objects.create(sucursalid=self.branch, productoid=self.product, cantidad=0)
        self.device, self.code = hybrid.create_device(actor=self.user, point=self.point, name="PC prueba")
        self.secret = "a"*64
        hybrid.enroll({"code": self.code, "secret": self.secret})
        self.device.refresh_from_db()
        self.session = hybrid.start_session(self.device, {"username":"hibrido", "password":"testing-pass", "session_id":str(uuid4())})
        self.product_data = hybrid.catalog(self.device, {"session_id": self.session["session_id"]})["updates"][0]

    def payload(self, **kwargs):
        data = {"protocol":1, "operation_id":str(uuid4()), "session_id":self.session["session_id"], "sequence":1,
            "occurred_at":timezone.now().isoformat(), "cash_received":"2000", "items":[{"id":self.product.pk,"quantity":500,"quote":self.product_data["quote"]}]}
        data.update(kwargs)
        return data


@override_settings(SECRET_KEY="hybrid-test-key-no-production", ALLOWED_HOSTS=["testserver"])
class HybridTests(HybridFixture, TestCase):
    def test_session_identifies_original_sale_draft_scope(self):
        self.assertEqual(self.session["user_id"], self.user.pk)
        self.assertEqual(self.session["branch_id"], self.branch.pk)
        self.assertEqual(self.session["point_id"], self.point.pk)
        self.assertEqual(self.session["turn_id"], self.turn.pk)

    def test_accepts_negative_stock_and_retries_once(self):
        payload = self.payload()
        first = hybrid.accept_sale(self.device, payload)
        self.assertEqual(first, hybrid.accept_sale(self.device, payload))
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(OperacionHibrida.objects.count(), 1)
        self.stock.refresh_from_db(); self.point.refresh_from_db()
        self.assertEqual(self.stock.cantidad, -500)
        self.assertEqual(self.point.dinerocaja, Decimal("1900"))
        self.assertEqual(first["change"], "100")
        self.assertIn("CAMBIO", first["receipt_text"])

    def test_id_conflict_sequence_and_signature_reject_without_writes(self):
        with self.assertRaises(hybrid.HybridError):
            hybrid.accept_sale(self.device, self.payload(sequence=2))
        with self.assertRaises(hybrid.HybridError):
            hybrid.accept_sale(self.device, self.payload(items=[{"id":self.product.pk,"quantity":1,"quote":"tampered"}]))
        self.assertFalse(Venta.objects.exists())
        p = self.payload(); hybrid.accept_sale(self.device, p)
        p["cash_received"] = "3000"
        with self.assertRaises(hybrid.HybridError):
            hybrid.accept_sale(self.device, p)
        self.assertEqual(Venta.objects.count(), 1)

    def test_pending_turn_cannot_begin_close_model_or_api(self):
        self.turn.estado = "CIERRE"
        with self.assertRaises(ValidationError):
            self.turn.save()
        for view, kwargs in ((TurnoCajaIniciarCierreApi,{}),(TurnoCajaIniciarCierreAPI,{"turno_id":self.turn.pk})):
            request = RequestFactory().post("/", {"turno_id":self.turn.pk}); request.user = self.user
            response = view.as_view()(request, **kwargs)
            self.assertEqual(response.status_code, 409)
            self.assertIn("híbrida", json.loads(response.content)["error"])
        hybrid.release_session(self.device, {"session_id":self.session["session_id"], "sequence":0})
        self.turn.save()

    def test_release_wrong_sequence_preserves_lease(self):
        hybrid.accept_sale(self.device, self.payload())
        with self.assertRaises(hybrid.HybridError):
            hybrid.release_session(self.device, {"session_id":self.session["session_id"], "sequence":0})
        self.assertIsNone(SesionHibrida.objects.get().liberada_en)

    def test_expired_lease_accepts_only_sales_within_authorization(self):
        original = self.payload()
        session = SesionHibrida.objects.get()
        session.vence_en = timezone.now()-timedelta(seconds=1)
        session.save()
        with self.assertRaises(hybrid.HybridError):
            hybrid.accept_sale(self.device, original)
        session.creada_en = timezone.now()-timedelta(hours=13)
        session.save()
        original["occurred_at"] = (timezone.now()-timedelta(hours=2)).isoformat()
        result = hybrid.accept_sale(self.device, original)
        self.assertEqual(result["status"], "accepted")

    def test_small_clock_skew_uses_authorized_start_and_retries_once(self):
        session = SesionHibrida.objects.get()
        data = self.payload(occurred_at=(session.creada_en-timedelta(seconds=2)).isoformat())
        result = hybrid.accept_sale(self.device, data)
        self.assertEqual(result, hybrid.accept_sale(self.device, data))
        self.assertEqual(OperacionHibrida.objects.get().ocurrida_en, session.creada_en)
        self.assertEqual(Venta.objects.count(), 1)
        self.assertEqual(Venta.objects.get().fecha, timezone.localtime(session.creada_en).date())

    def test_clock_margin_does_not_allow_old_future_or_expired_sales(self):
        session = SesionHibrida.objects.get()
        for stamp in (session.creada_en-timedelta(minutes=3), timezone.now()+timedelta(minutes=3), session.vence_en+timedelta(seconds=1)):
            with self.subTest(stamp=stamp), self.assertRaises(hybrid.HybridError):
                hybrid.accept_sale(self.device, self.payload(occurred_at=stamp.isoformat()))
        self.assertFalse(Venta.objects.exists())

    def test_api_requires_device_and_ignores_cookie_auth(self):
        self.client.force_login(self.user)
        r = self.client.post("/api/hybrid/v1/sale/", data=json.dumps(self.payload()), content_type="application/json")
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/api/hybrid/v1/sale/", data=json.dumps(self.payload()), content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.device.pk}.{self.secret}")
        self.assertEqual(r.status_code, 200, r.content)

    def test_existing_ack_is_available_after_disable_new_sales_block(self):
        p = self.payload(); expected = hybrid.accept_sale(self.device, p)
        ConfiguracionFuncionalidad.objects.filter(clave=HYBRID_POS_FEATURE).update(habilitada=False)
        clear_feature_cache()
        self.assertEqual(hybrid.accept_sale(self.device,p), expected)
        with self.assertRaises(hybrid.HybridError):
            hybrid.accept_sale(self.device,self.payload(sequence=2))

    def test_catalog_delta_and_ptm_exclusion(self):
        known = {str(self.product.pk): self.product_data["digest"]}
        result = hybrid.catalog(self.device,{"session_id":self.session["session_id"],"known":known})
        self.assertFalse(result["updates"])
        Producto.objects.filter(pk=self.product.pk).update(precio=4)
        result = hybrid.catalog(self.device,{"session_id":self.session["session_id"],"known":known})
        self.assertEqual(result["updates"][0]["price"], "4.00")
        Producto.objects.filter(pk=self.product.pk).update(tipo_ptm="retiro")
        result = hybrid.catalog(self.device,{"session_id":self.session["session_id"],"known":known})
        self.assertEqual(result["deleted"], [str(self.product.pk)])

    def test_cannot_rebind_or_start_another_session(self):
        with self.assertRaises(hybrid.HybridError):
            hybrid.enroll({"code":self.code,"secret":"b"*64})
        with self.assertRaises(hybrid.HybridError):
            hybrid.start_session(self.device,{"username":"hibrido","password":"testing-pass","session_id":str(uuid4())})

    def test_inactive_user_cannot_submit(self):
        self.user.is_active=False; self.user.save()
        with self.assertRaises(hybrid.HybridError):
            hybrid.accept_sale(self.device,self.payload())
        self.assertFalse(Venta.objects.exists())

    def test_outer_transaction_rolls_back_if_receipt_ledger_cannot_save(self):
        with patch("mainApp.services.hybrid.OperacionHibrida.objects.create", side_effect=RuntimeError("simulated")):
            with self.assertRaises(RuntimeError):
                hybrid.accept_sale(self.device,self.payload())
        self.assertFalse(Venta.objects.exists())
        self.stock.refresh_from_db(); self.assertEqual(self.stock.cantidad,0)

    def test_local_to_real_django_offline_lost_ack_and_release(self):
        # Reuse the authorized session and quote, but real sale ingestion and real inventory.
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder); store.set("session",self.session); store.set("ready",True)
            store.merge_catalog({"updates":[self.product_data],"deleted":[]})
            state={"offline":True,"lost":True}
            def transport(action,data):
                if state["offline"]: raise RemoteError("offline")
                if action=="sale":
                    result=hybrid.accept_sale(self.device,data)
                    if state["lost"]:
                        state["lost"]=False
                        raise RemoteError("ack lost")
                    return result
                return {"catalog":hybrid.catalog,"release":hybrid.release_session}[action](self.device,data)
            local=LocalClient(store,transport=transport);local.unlocked=True
            result=local.checkout({"operation_id":str(uuid4()),"items":[{"id":self.product.pk,"quantity":500}],"cash_received":"2000"})
            self.assertEqual(result["state"],"pending");self.assertFalse(Venta.objects.exists())
            state["offline"]=False
            local.synchronize(); self.assertEqual(len(store.pending()),1)
            local.synchronize(); self.assertFalse(store.pending())
            self.assertEqual(Venta.objects.count(),1)
            local.release(); self.assertIsNotNone(SesionHibrida.objects.get().liberada_en)

    def test_two_devices_add_deltas_instead_of_overwriting_stock(self):
        point = PuntosPago.objects.create(nombre="Caja 2", sucursalid=self.branch)
        user = Usuario.objects.create_user("hibrido2", "test-pass", rolid=self.user.rolid)
        Empleado.objects.create(usuarioid=user, sucursalid=self.branch, nombre="Otro", apellido="Cajero", telefono="102", email="other@example.test", numerodocumento="124")
        TurnoCaja.objects.create(puntopago=point,cajero=user)
        device,code=hybrid.create_device(actor=self.user,point=point,name="Segundo PC")
        hybrid.enroll({"code":code,"secret":"b"*64})
        session=hybrid.start_session(device,{"username":"hibrido2","password":"test-pass","session_id":str(uuid4())})
        product=hybrid.catalog(device,{"session_id":session["session_id"]})["updates"][0]
        first=self.payload();hybrid.accept_sale(self.device,first)
        second=self.payload(session_id=session["session_id"],cash_received="3000",items=[{"id":self.product.pk,"quantity":700,"quote":product["quote"]}])
        hybrid.accept_sale(device,second)
        hybrid.accept_sale(self.device,first);hybrid.accept_sale(device,second)
        self.stock.refresh_from_db();self.assertEqual(self.stock.cantidad,-1200)
        self.assertEqual(Venta.objects.count(),2)
