import json
from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Rol, Usuario, VentaCarritoAudit
from .views import VentaCarritoLimpioAuditView, _record_cart_clear_with_daily_count


@override_settings(TIME_ZONE="America/Bogota")
class CartClearDailyCountTests(TestCase):
    def setUp(self):
        self.user = Usuario.objects.create_user("Cajero contador")
        self.other = Usuario.objects.create_user("Otro cajero")
        self.payload = {
            "items": [{"producto_id": 123, "nombre": "LECHE", "cantidad": 2,
                       "precio": "3000", "subtotal": "6000"}],
            "total": "6000",
        }

    def send(self, payload=None, user=None):
        request = RequestFactory().post(
            reverse("venta_carrito_limpio_audit"),
            data=json.dumps(self.payload if payload is None else payload),
            content_type="application/json",
        )
        request.user = self.user if user is None else user
        return VentaCarritoLimpioAuditView.as_view()(request)

    def test_daily_number_is_per_worker_not_the_global_audit_id(self):
        VentaCarritoAudit.objects.create(usuarioid=self.other.pk)
        VentaCarritoAudit.objects.create(usuarioid=self.user.pk, creado_en=timezone.now() - timedelta(days=1))
        for expected in (1, 2):
            response = self.send()
            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)
            self.assertEqual(data["daily_count"], expected)
            self.assertNotEqual(data["audit_id"], expected)
            self.assertEqual(data["audit_day"], timezone.localdate().isoformat())
        self.assertEqual(json.loads(self.send(user=self.other).content)["daily_count"], 2)

    def test_existing_records_are_included_across_cashboxes(self):
        VentaCarritoAudit.objects.create(usuarioid=self.user.pk, puntopagoid=12)
        VentaCarritoAudit.objects.create(usuarioid=self.user.pk, puntopagoid=34)
        self.assertEqual(json.loads(self.send().content)["daily_count"], 3)

    def test_count_resets_at_colombian_midnight_not_utc_midnight(self):
        utc = datetime_timezone.utc
        before_midnight = datetime(2026, 9, 10, 4, 59, 59, tzinfo=utc)
        VentaCarritoAudit.objects.create(
            usuarioid=self.user.pk, creado_en=datetime(2026, 9, 9, 5, 0, tzinfo=utc),
        )
        # Un registro del día anterior y otro del siguiente no deben sumarse.
        VentaCarritoAudit.objects.create(
            usuarioid=self.user.pk, creado_en=datetime(2026, 9, 9, 4, 59, 59, tzinfo=utc),
        )
        VentaCarritoAudit.objects.create(
            usuarioid=self.user.pk, creado_en=datetime(2026, 9, 10, 5, 0, tzinfo=utc),
        )
        # La zona activada por una petición no cambia el día del negocio.
        with timezone.override("Asia/Tokyo"):
            _, count, day = _record_cart_clear_with_daily_count(self.user, creado_en=before_midnight)
        self.assertEqual(count, 2)
        self.assertEqual(day.isoformat(), "2026-09-09")
        _, count, day = _record_cart_clear_with_daily_count(
            self.user, creado_en=datetime(2026, 9, 11, 5, 0, tzinfo=utc),
        )
        self.assertEqual(count, 1)
        self.assertEqual(day.isoformat(), "2026-09-11")

    def test_tab_close_backups_are_saved_but_not_counted_as_cart_clears(self):
        response = self.send({**self.payload, "motivo": "cierre_sin_borrador"})
        self.assertEqual(json.loads(response.content)["daily_count"], 0)
        VentaCarritoAudit.objects.create(usuarioid=self.user.pk, evento="otro_evento")
        response = self.send()
        self.assertEqual(json.loads(response.content)["daily_count"], 1)
        self.assertEqual(VentaCarritoAudit.objects.filter(motivo="cierre_sin_borrador").count(), 1)

    def test_browser_cannot_supply_the_user_count_or_day(self):
        response = self.send({**self.payload, "usuarioid": self.other.pk,
                              "daily_count": 999, "enviado_en": "2000-01-01T00:00:00Z"})
        data = json.loads(response.content)
        audit = VentaCarritoAudit.objects.get(pk=data["audit_id"])
        self.assertEqual(audit.usuarioid, self.user.pk)
        self.assertEqual(audit.motivo, "carrito_vaciado")
        self.assertEqual(data["daily_count"], 1)
        self.assertEqual(data["audit_day"], timezone.localdate().isoformat())
        self.assertEqual(audit.productos[0]["cantidad"], "2.000")

    def test_web_master_remains_exempt(self):
        self.user.rolid = Rol.objects.create(nombre="Web Master")
        data = json.loads(self.send().content)
        self.assertTrue(data["ignored"])
        self.assertNotIn("daily_count", data)
        self.assertEqual(VentaCarritoAudit.objects.count(), 0)

    def test_invalid_or_empty_carts_do_not_increase_the_count(self):
        for payload in ({"items": []}, {"items": [{}]}, {"items": "bad"}, [], None):
            # None normalmente selecciona el payload válido en el helper.
            invalid = payload if payload is not None else {"items": [None]}
            with self.subTest(payload=invalid):
                self.assertEqual(self.send(invalid).status_code, 400)
        self.assertEqual(VentaCarritoAudit.objects.count(), 0)

    def test_authentication_is_required(self):
        self.assertEqual(self.send(user=AnonymousUser()).status_code, 302)
        self.assertEqual(VentaCarritoAudit.objects.count(), 0)

    def test_worker_row_is_locked_to_serialize_concurrent_tabs(self):
        with patch.object(Usuario.objects, "select_for_update", wraps=Usuario.objects.select_for_update) as lock:
            self.assertEqual(self.send().status_code, 200)
        lock.assert_called_once_with()


class CartClearNoticeMarkupTests(SimpleTestCase):
    def test_floating_notice_is_accessible_and_outside_the_sales_panels(self):
        template = (settings.BASE_DIR / "mainApp/templates/generar_venta.html").read_text(encoding="utf-8")
        styles = (settings.BASE_DIR / "mainApp/static/css/generar_venta.css").read_text(encoding="utf-8")
        self.assertIn('id="venta-carrito-audit-notice"', template)
        self.assertIn('role="status" aria-live="polite" aria-atomic="true" hidden', template)
        self.assertIn('id="venta-carrito-audit-message"', template)
        self.assertIn('.venta-carrito-audit-notice[hidden]{ display:none; }', styles)
        self.assertIn('</div>\n\n<p id="venta-carrito-audit-notice"', template)
        toast_styles = styles.split('.venta-carrito-audit-notice{', 1)[1].split('}', 1)[0]
        self.assertIn('position:fixed', toast_styles)
        self.assertIn('pointer-events:none', toast_styles)
        self.assertIn('font:600 16px/1.45 var(--font)', toast_styles)

    def test_successful_sale_reset_does_not_register_a_cart_clear(self):
        script = (settings.BASE_DIR / "mainApp/static/javascript/generar_venta.js").read_text(encoding="utf-8")
        start = script.index("function clearCartAndTotals()")
        end = script.index('window.addEventListener("pageshow"', start)
        self.assertNotIn("sendCartClearAudit", script[start:end])
