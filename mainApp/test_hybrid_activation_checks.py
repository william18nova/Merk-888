"""Read-only activation checks against a disposable PostgreSQL database."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import SimpleTestCase, TransactionTestCase

from mainApp.models import (ConfiguracionFuncionalidad, Empleado, MetodoPago,
                            PuntosPago, Rol, Sucursal, TurnoCaja, Usuario)
from mainApp.services.feature_flags import TURN_REQUIRED_FEATURE
from mainApp.services.hybrid_activation_checks import (
    ActivationCheckError, database_failure_reason, inspect_source)


class ActivationSourceTests(TransactionTestCase):
    def setUp(self):
        self.role = Rol.objects.create(nombre="Web Master")
        self.user = Usuario.objects.create_user("activation-only-test", "fictional", rolid=self.role)
        self.branch = Sucursal.objects.create(nombre="Fictional branch")
        self.employee = Empleado.objects.create(usuarioid=self.user, sucursalid=self.branch,
            nombre="William", apellido="Nova", numerodocumento="FICTIONAL-ACTIVATION")
        self.point = PuntosPago.objects.create(nombre="Caja menor", sucursalid=self.branch)
        self.feature = ConfiguracionFuncionalidad.objects.create(clave=TURN_REQUIRED_FEATURE, habilitada=False)
        MetodoPago.objects.create(codigo="efectivo", nombre="Efectivo", activo=True, es_efectivo=True)
        self.recorder = MigrationRecorder(connection)
        self.recorder.ensure_schema()
        if ("mainApp", "0049_solicitudpos") not in self.recorder.applied_migrations():
            self.recorder.record_applied("mainApp", "0049_solicitudpos")

    def inspect(self):
        return inspect_source("William Nova", "Caja menor")

    def test_exact_selection_and_read_only_transaction(self):
        from mainApp.services.hybrid_activation_checks import _inspect
        def checked(*args, **kwargs):
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                self.assertEqual(cursor.fetchone()[0], "on")
            return _inspect(*args, **kwargs)
        with patch("mainApp.services.hybrid_activation_checks._inspect", side_effect=checked):
            result = inspect_source("  WILLIAM  NOVA ", "CAJA MENOR")
        self.assertEqual(result["user_id"], self.user.pk)
        self.assertEqual(result["point_id"], self.point.pk)
        self.assertIsNone(result["turn_id"])
        self.assertEqual(TurnoCaja.objects.count(), 0)

    def test_explicit_ids_and_login_name(self):
        self.assertEqual(inspect_source(user_id=self.user.pk, point_id=self.point.pk)["user_id"], self.user.pk)
        self.assertEqual(inspect_source(self.user.nombreusuario, "Caja menor")["user_id"], self.user.pk)

    def test_prefix_is_not_silently_chosen(self):
        with self.assertRaisesRegex(ActivationCheckError, "coincidencia única"):
            inspect_source("William", "Caja menor")

    def test_duplicate_full_name_requires_explicit_id(self):
        other = Usuario.objects.create_user("another-fictional", "fictional", rolid=self.role)
        Empleado.objects.create(usuarioid=other, sucursalid=self.branch,
            nombre="William", apellido="Nova", numerodocumento="FICTIONAL-OTHER",
            telefono="FICTIONAL-OTHER", email="other@example.invalid")
        with self.assertRaisesRegex(ActivationCheckError, "usuario-id"):
            self.inspect()

    def test_other_branch_cannot_be_selected(self):
        branch = Sucursal.objects.create(nombre="Other fictional branch")
        point = PuntosPago.objects.create(nombre="Caja menor", sucursalid=branch)
        self.assertEqual(self.inspect()["point_id"], self.point.pk)
        with self.assertRaisesRegex(ActivationCheckError, "sucursal"):
            inspect_source(user_id=self.user.pk, point_id=point.pk)

    def test_disabled_user_and_permission_denied(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        with self.assertRaisesRegex(ActivationCheckError, "inactivo"):
            self.inspect()
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        with patch("mainApp.permissions.user_can_access_url_name", return_value=False):
            with self.assertRaisesRegex(ActivationCheckError, "permiso"):
                self.inspect()

    def test_missing_receipt_migration_is_not_applied_automatically(self):
        self.recorder.record_unapplied("mainApp", "0049_solicitudpos")
        with self.assertRaisesRegex(ActivationCheckError, "no figura aplicada"):
            self.inspect()
        self.assertNotIn(("mainApp", "0049_solicitudpos"), self.recorder.applied_migrations())

    def test_required_turn_is_not_opened_automatically(self):
        self.feature.habilitada = True
        self.feature.save()
        with self.assertRaisesRegex(ActivationCheckError, "turno abierto"):
            self.inspect()
        self.assertEqual(TurnoCaja.objects.count(), 0)
        turn = TurnoCaja.objects.create(cajero=self.user, puntopago=self.point, estado="ABIERTO")
        self.assertEqual(self.inspect()["turn_id"], turn.pk)

    def test_missing_payment_methods(self):
        MetodoPago.objects.update(activo=False)
        with self.assertRaisesRegex(ActivationCheckError, "medios de pago"):
            self.inspect()

    def test_accidental_source_write_is_rejected_and_settings_restored(self):
        original_settings = connection.settings_dict
        def bad_change(*args, **kwargs):
            Usuario.objects.filter(pk=self.user.pk).update(is_active=False)
        with patch("mainApp.services.hybrid_activation_checks._inspect", side_effect=bad_change):
            with self.assertRaises(ActivationCheckError):
                self.inspect()
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.assertIs(connection.settings_dict, original_settings)


class ActivationCommandTests(SimpleTestCase):
    def test_sensitive_driver_details_are_never_reported(self):
        for detail in ("timeout expired", "password authentication failed", "SSL error",
                       "could not translate host name", "network is unreachable", "unexpected failure"):
            message = database_failure_reason(Exception(detail + " password=PRIVATE-TEST-SECRET"))
            self.assertNotIn("PRIVATE-TEST-SECRET", message)

    def test_invalid_selection_fails_before_connecting(self):
        for kwargs in ({}, {"user_id": -1, "point_id": 1}):
            with self.assertRaises(ActivationCheckError):
                inspect_source(**kwargs)

    def test_command_does_not_activate_or_diagnose_engine_without_option(self):
        output = StringIO()
        result = {"user_id": 1, "user_name": "William Nova", "point_id": 2,
                  "point_name": "Caja menor", "branch_name": "Fictional", "required_turn": False}
        module = "mainApp.management.commands.comprobar_activacion_hibrida"
        with patch(module + ".load_config", return_value={"activation": "database_only", "port": 55460}), \
             patch(module + ".inspect_source", return_value=result), \
             patch(module + ".call_command") as diagnostic, patch("local_pos.runtime.save_json") as save:
            call_command("comprobar_activacion_hibrida", usuario="William Nova", caja="Caja menor", stdout=output)
        diagnostic.assert_not_called()
        save.assert_not_called()
        self.assertIn("NO activa ventas offline", output.getvalue())

    def test_command_connection_failure_preserves_config(self):
        module = "mainApp.management.commands.comprobar_activacion_hibrida"
        with patch(module + ".load_config", return_value={"activation": "database_only", "port": 55460}), \
             patch(module + ".inspect_source", side_effect=ActivationCheckError("Tiempo de espera agotado")), \
             patch("local_pos.runtime.save_json") as save:
            with self.assertRaisesRegex(CommandError, "No se cambió la configuración"):
                call_command("comprobar_activacion_hibrida", usuario="William Nova", caja="Caja menor", stdout=StringIO())
        save.assert_not_called()
