from contextlib import nullcontext
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
import importlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import reverse

from .models import NotificacionNequi
from .views import (
    NequiNotificacionesDisponiblesView,
    NequiNotificationWebhookView,
    _looks_like_nequi_payment,
    _parse_nequi_amount,
)


class NequiIncomingClassifierTests(SimpleTestCase):
    def test_accepts_known_incoming_nequi_formats(self):
        cases = (
            (
                "Te enviaron plata por Bre-B",
                "Te enviaron $21.000. Entra a tu app y revisa tu saldo.",
            ),
            ("Envio", "Ana Ruiz te envió 10000, lo mejor!"),
            ("Pago recibido", "Recibiste un pago de $15.000"),
            ("Transferencia recibida", "Te consignaron $8.000"),
        )

        for title, text in cases:
            with self.subTest(title=title, text=text):
                amount = _parse_nequi_amount(f"{title} {text}")
                self.assertTrue(
                    _looks_like_nequi_payment(title, text, amount),
                )

    def test_rejects_outgoing_purchases_withdrawals_and_refunds(self):
        cases = (
            (
                "Tu plata llegó con éxito",
                "Envío exitoso, la plata ya está en el Nequi destino por $50.000.",
            ),
            (
                "Envío de plata exitoso",
                "Te contamos que el envío de plata por $100.000 fue exitoso.",
            ),
            ("Pago exitoso por PSE", "Hiciste un pago por $70.000 y todo salió bien."),
            ("Compra exitosa con tarjeta Nequi", "Pagaste $9.500 en un comercio."),
            ("Retiro en cajero", "Sacaste $40.000"),
            ("Recarga PSE", "Tu recarga de $20.000 fue un éxito."),
            ("Devolución", "Recibiste $12.000 de vuelta."),
            ("Aviso", "Este envío de $30.000 continúa en proceso."),
        )

        for title, text in cases:
            with self.subTest(title=title, text=text):
                amount = _parse_nequi_amount(f"{title} {text}")
                self.assertFalse(
                    _looks_like_nequi_payment(title, text, amount),
                )

    def test_model_defaults_to_not_received_until_classified(self):
        field = NotificacionNequi._meta.get_field("es_ingreso")

        self.assertFalse(field.default)
        self.assertTrue(field.db_index)

    def test_schema_and_historical_classification_use_separate_migrations(self):
        schema_migration = importlib.import_module(
            "mainApp.migrations.0031_notificacionnequi_es_ingreso"
        )
        data_migration = importlib.import_module(
            "mainApp.migrations.0032_classify_nequi_incoming_notifications"
        )

        self.assertEqual(
            schema_migration.Migration.dependencies,
            [("mainApp", "0030_rename_batavia_completa")],
        )
        self.assertEqual(
            data_migration.Migration.dependencies,
            [("mainApp", "0031_notificacionnequi_es_ingreso")],
        )
        self.assertEqual(len(schema_migration.Migration.operations), 1)
        self.assertEqual(len(data_migration.Migration.operations), 1)
        self.assertTrue(data_migration._is_incoming_notification(
            "Te enviaron plata por Bre-B",
            "Te enviaron $21.000.",
            Decimal("21000"),
        ))
        self.assertFalse(data_migration._is_incoming_notification(
            "Aviso",
            "Este envío de $30.000 continúa en proceso.",
            Decimal("30000"),
        ))


class NequiIncomingWebhookTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(MACRODROID_NEQUI_TOKEN="test-nequi-token")
    def test_outgoing_nequi_notification_is_ignored_without_writing(self):
        request = self.factory.post(
            reverse("macrodroid_nequi_webhook"),
            data=json.dumps({
                "app": "Nequi",
                "package": "com.nequi.MobileApp",
                "title": "Envío de plata exitoso",
                "text": (
                    "Te contamos que el envío de plata por $100.000 "
                    "fue exitoso."
                ),
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-nequi-token",
        )

        with (
            patch("mainApp.views.is_feature_enabled", return_value=True),
            patch(
                "mainApp.views.NotificacionNequi.objects.get_or_create",
            ) as create_notification,
        ):
            response = NequiNotificationWebhookView().post(request)

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["ignored"])
        self.assertIn("dinero recibido", payload["reason"])
        create_notification.assert_not_called()

    @override_settings(MACRODROID_NEQUI_TOKEN="test-nequi-token")
    def test_notification_from_another_app_is_ignored(self):
        request = self.factory.post(
            reverse("macrodroid_nequi_webhook"),
            data=json.dumps({
                "app": "Otra billetera",
                "package": "com.example.wallet",
                "title": "Te enviaron dinero",
                "text": "Te enviaron $25.000.",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-nequi-token",
        )

        with (
            patch("mainApp.views.is_feature_enabled", return_value=True),
            patch(
                "mainApp.views.NotificacionNequi.objects.get_or_create",
            ) as create_notification,
        ):
            response = NequiNotificationWebhookView().post(request)

        payload = json.loads(response.content)
        self.assertEqual(response.status_code, 202)
        self.assertTrue(payload["ignored"])
        self.assertIn("no proviene de Nequi", payload["reason"])
        create_notification.assert_not_called()

    @override_settings(MACRODROID_NEQUI_TOKEN="test-nequi-token")
    def test_empty_notification_is_rejected_as_invalid(self):
        request = self.factory.post(
            reverse("macrodroid_nequi_webhook"),
            data=json.dumps({"app": "Nequi", "package": "com.nequi.MobileApp"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-nequi-token",
        )

        with (
            patch("mainApp.views.is_feature_enabled", return_value=True),
            patch(
                "mainApp.views.NotificacionNequi.objects.get_or_create",
            ) as create_notification,
        ):
            response = NequiNotificationWebhookView().post(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("sin titulo ni texto", json.loads(response.content)["error"])
        create_notification.assert_not_called()

    @override_settings(MACRODROID_NEQUI_TOKEN="test-nequi-token")
    def test_incoming_notification_is_saved_as_received_money(self):
        request = self.factory.post(
            reverse("macrodroid_nequi_webhook"),
            data=json.dumps({
                "event_id": "incoming-1",
                "app": "Nequi",
                "package": "com.nequi.MobileApp",
                "title": "Te enviaron plata por Bre-B",
                "text": "Te enviaron $21.000. Entra a tu app y revisa tu saldo.",
            }),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer test-nequi-token",
        )
        notification = SimpleNamespace(
            notificacionid=91,
            titulo="Te enviaron plata por Bre-B",
            texto="Te enviaron $21.000. Entra a tu app y revisa tu saldo.",
            app="Nequi",
            paquete="com.nequi.MobileApp",
            monto=Decimal("21000.00"),
            remitente="",
            referencia="",
            recibido_en=datetime.now(dt_timezone.utc),
            venta_id=None,
        )

        with (
            patch("mainApp.views.is_feature_enabled", return_value=True),
            patch(
                "mainApp.views.transaction.atomic",
                return_value=nullcontext(),
            ),
            patch("mainApp.views.locked_feature_enabled", return_value=True),
            patch(
                "mainApp.views.NotificacionNequi.objects.get_or_create",
                return_value=(notification, True),
            ) as create_notification,
        ):
            response = NequiNotificationWebhookView().post(request)

        self.assertEqual(response.status_code, 201)
        defaults = create_notification.call_args.kwargs["defaults"]
        self.assertTrue(defaults["es_ingreso"])
        self.assertEqual(defaults["monto"], Decimal("21000.00"))

    def test_available_payments_query_requires_received_money(self):
        request = self.factory.get(
            reverse("nequi_notificaciones_disponibles"),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        request.user = SimpleNamespace(is_authenticated=True)

        with (
            patch("mainApp.views.is_feature_enabled", return_value=True),
            patch(
                "mainApp.views.NotificacionNequi.objects.filter",
            ) as notifications,
        ):
            notifications.return_value.order_by.return_value.__getitem__.return_value = []
            response = NequiNotificacionesDisponiblesView().get(request)

        self.assertEqual(response.status_code, 200)
        notifications.assert_called_once_with(
            es_ingreso=True,
            venta__isnull=True,
            monto__isnull=False,
            monto__gt=0,
        )
