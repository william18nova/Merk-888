import importlib
import json
from datetime import date, timedelta
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.apps import apps
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import connection
from django.db.migrations.state import ModelState, ProjectState
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from mainApp.models import (
    CambioRotacionEmpleado, Empleado, MiembroRotacionEmpleado, Rol, RotacionEmpleado,
    Sucursal, TelegramAccionPendiente, TelegramUsuario, TurnoEmpleado, Usuario,
)
from mainApp.services import employee_rotation as rotation
from mainApp.services import employee_schedule as schedule
from mainApp.services import telegram_bot as bot


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True)
class RotationTests(TestCase):
    def setUp(self):
        cache.clear()
        self.today = patch("mainApp.services.employee_rotation.timezone.localdate", return_value=date(2026, 9, 7))
        self.today.start()
        self.addCleanup(self.today.stop)
        self.admin = Usuario.objects.create_user("William calendario", rolid=Rol.objects.create(nombre="Web Master"))
        self.worker = Usuario.objects.create_user("Empleado calendario")
        self.branch = Sucursal.objects.create(nombre="Yerbabuena")
        Empleado.objects.bulk_create([
            Empleado(nombre=name, apellido="Pruebas", telefono=str(index), email=f"{index}@example.test", numerodocumento=str(index), sucursalid=self.branch, usuarioid=self.worker if name == "Camila" else None)
            for index, name in enumerate(rotation.preset()["personas"], 1)
        ])
        self.mapping = rotation.suggested_mapping()
        self.profile = TelegramUsuario.objects.create(usuario=self.admin, telegram_user_id=7770, telegram_chat_id=7770)
        self.worker_profile = TelegramUsuario.objects.create(usuario=self.worker, telegram_user_id=7771, telegram_chat_id=7771)
        self.client_stub = SimpleNamespace(answer_callback=MagicMock())

    def payload(self, **changes):
        return dict({"inicio": "2026-09-07", "empleados": self.mapping, "sucursal_id": self.branch.pk, "solicitud_id": str(uuid4())}, **changes)

    def create(self, **changes):
        return RotacionEmpleado.objects.get(pk=rotation.create_rotation(self.admin, self.payload(**changes))[0]["id"])

    def event(self, cycle, name="Cristian", week=1, day=0, repeat=0):
        slot = next(item for item in cycle.patron if item["empleado_id"] == self.mapping[name] and item["semana"] == week and item["dia"] == day)
        base = cycle.inicio + timedelta(days=(week-1)*7 + day + repeat*28)
        return rotation.occurrence(f"r{cycle.pk}-{slot['clave']}-{base:%Y%m%d}")[3]

    def change(self, event, **changes):
        return dict({"operacion": "editar", "id": event["id"], "version": event["version"], "alcance": "fecha", "solicitud_id": str(uuid4())}, **changes)

    def test_exact_four_weeks_and_approved_hours(self):
        cycle = self.create()
        self.assertEqual(rotation.cycle_days(cycle), 28)
        self.assertEqual(len(cycle.patron), 200)
        self.assertEqual(sum(not slot["descanso"] for slot in cycle.patron), 180)
        self.assertEqual(sum(slot["duracion"] for slot in cycle.patron if not slot["descanso"]), 1220 * 60)
        for week in range(1, 5):
            morning = self.event(cycle, "Jackeline", week, 0)
            self.assertEqual((morning["inicio"][11:16], morning["fin"][11:16]), ("07:00", "14:00"))
            night = self.event(cycle, "Claudia", week, 6)
            self.assertEqual((night["inicio"][11:16], night["fin"][11:16]), ("20:00", "01:00"))
            self.assertEqual((schedule.parse_time(night["fin"]) - schedule.parse_time(night["inicio"])).total_seconds(), 5*3600)
            special = self.event(cycle, "Duvan", week, 4)
            self.assertEqual((special["inicio"][11:16], special["fin"][11:16]), ("15:00", "00:00"))
        self.assertTrue(self.event(cycle, "Santiago", 1, 0)["descanso"])
        self.assertFalse(self.event(cycle, "Santiago", 1, 1)["descanso"])
        self.assertFalse(TurnoEmpleado.objects.exists())

    def test_fourth_week_returns_to_first_without_horizon(self):
        cycle = self.create()
        for repetition in (1, 5, 100, 500):
            event = self.event(cycle, repeat=repetition)
            self.assertEqual(event["inicio"][:10], (date(2026, 9, 7) + timedelta(days=28*repetition)).isoformat())
            self.assertEqual(event["semana_rotacion"], 1)
        self.assertEqual(rotation._date("20260907"), date(2026, 9, 7))

    def test_preset_matches_every_named_assignment_without_duplicates(self):
        cycle = self.create()
        for week, days in enumerate(rotation.preset()["semanas"], 1):
            for day, groups in enumerate(days):
                expected = {self.mapping[name] for names in groups for name in names}
                slots = [slot for slot in cycle.patron if slot["semana"] == week and slot["dia"] == day]
                self.assertEqual(len(expected), len(slots))
                self.assertEqual(expected, {slot["empleado_id"] for slot in slots})

    def test_single_date_edit_does_not_change_future_pattern(self):
        cycle = self.create()
        event = self.event(cycle)
        result, _ = schedule.save_change(self.admin, self.change(event, inicio="2026-09-07T08:00", fin="2026-09-07T14:00"))
        self.assertTrue(result["excepcion"])
        self.assertEqual(self.event(cycle)["inicio"][11:16], "08:00")
        self.assertEqual(self.event(cycle, repeat=1)["inicio"][11:16], "07:00")

    def test_future_edit_changes_same_slot_only_and_not_history(self):
        cycle = self.create()
        event = self.event(cycle, repeat=1)
        schedule.save_change(self.admin, self.change(event, alcance="futuro", inicio="2026-10-05T08:00", fin="2026-10-05T13:00"))
        self.assertEqual(self.event(cycle)["inicio"][11:16], "07:00")
        self.assertEqual(self.event(cycle, repeat=2)["inicio"][11:16], "08:00")
        self.assertEqual(self.event(cycle, week=2, repeat=2)["inicio"][11:16], "07:00")

    def test_future_revision_preserves_other_dates_explicit_exceptions(self):
        cycle = self.create()
        schedule.save_change(self.admin, self.change(self.event(cycle, repeat=2), notas="Excepción"))
        schedule.save_change(self.admin, self.change(self.event(cycle, repeat=1), alcance="futuro", notas="Nuevo patrón"))
        self.assertEqual(self.event(cycle, repeat=2)["notas"], "Excepción")
        self.assertEqual(self.event(cycle, repeat=3)["notas"], "Nuevo patrón")

    def test_latest_future_edit_supersedes_older_future_revision(self):
        cycle = self.create()
        schedule.save_change(self.admin, self.change(self.event(cycle, repeat=3), alcance="futuro", notas="Programación anterior"))
        schedule.save_change(self.admin, self.change(self.event(cycle, repeat=1), alcance="futuro", notas="Última programación"))
        self.assertEqual(self.event(cycle, repeat=4)["notas"], "Última programación")

    def test_scope_required_and_stale_version_rejected(self):
        cycle = self.create()
        event = self.event(cycle)
        payload = self.change(event, notas="Cambio")
        del payload["alcance"]
        with self.assertRaises(schedule.ScheduleError):
            schedule.save_change(self.admin, payload)
        schedule.save_change(self.admin, self.change(event, notas="Cambio actual"))
        with self.assertRaises(schedule.ScheduleConflict):
            schedule.save_change(self.admin, self.change(event, notas="Cambio desactualizado"))

    def test_cancellation_scopes_and_idempotent_retries(self):
        cycle = self.create()
        payload = self.change(self.event(cycle), operacion="cancelar")
        schedule.save_change(self.admin, payload)
        self.assertFalse(schedule.save_change(self.admin, payload)[1])
        self.assertTrue(self.event(cycle)["cancelado"])
        self.assertFalse(self.event(cycle, repeat=1)["cancelado"])
        schedule.save_change(self.admin, self.change(self.event(cycle, repeat=1), operacion="cancelar", alcance="futuro"))
        self.assertTrue(self.event(cycle, repeat=50)["cancelado"])

    def test_import_is_atomic_and_does_not_duplicate_existing_rotation(self):
        payload = self.payload()
        result, _ = rotation.create_rotation(self.admin, payload)
        self.assertEqual(rotation.create_rotation(self.admin, payload), (result, False))
        with self.assertRaises(schedule.ScheduleConflict):
            self.create()
        self.assertEqual(RotacionEmpleado.objects.count(), 1)
        self.assertEqual(CambioRotacionEmpleado.objects.count(), 1)

    def test_single_conflict_rolls_back_and_preserves_version(self):
        cycle = self.create()
        event = self.event(cycle)
        with self.assertRaises(schedule.ScheduleConflict):
            schedule.save_change(self.admin, self.change(event, inicio="2026-09-08T07:00", fin="2026-09-08T14:00"))
        cycle.refresh_from_db()
        self.assertEqual(cycle.version, 1)
        self.assertEqual(CambioRotacionEmpleado.objects.count(), 1)

    def test_far_future_manual_conflict_is_not_ignored(self):
        cycle = self.create()
        future = self.event(cycle, repeat=100)["inicio"][:10]
        TurnoEmpleado.objects.create(empleado_id=self.mapping["Cristian"], sucursal=self.branch, inicio=schedule.parse_time(future+"T15:00"), fin=schedule.parse_time(future+"T16:00"))
        with self.assertRaises(schedule.ScheduleConflict):
            schedule.save_change(self.admin, self.change(self.event(cycle), alcance="futuro", fin="2026-09-07T16:00"))
        cycle.refresh_from_db()
        self.assertEqual(cycle.version, 1)

    def test_manual_schedule_cannot_overlap_rotation_or_rest(self):
        self.create()
        for name in ("Cristian", "Santiago"):
            with self.subTest(name=name), self.assertRaises(schedule.ScheduleConflict):
                schedule.save_change(self.admin, {"operacion": "crear", "empleado_id": self.mapping[name], "sucursal_id": self.branch.pk, "inicio": "2026-09-07T10:00", "fin": "2026-09-07T11:00", "solicitud_id": str(uuid4())})

    def test_rest_can_be_converted_to_work_with_explicit_hours(self):
        cycle = self.create()
        event = self.event(cycle, "Santiago")
        schedule.save_change(self.admin, self.change(event, descanso=False, inicio="2026-09-07T07:00", fin="2026-09-07T14:00"))
        self.assertFalse(self.event(cycle, "Santiago")["descanso"])
        self.assertTrue(self.event(cycle, "Santiago", repeat=1)["descanso"])

    def test_own_api_returns_only_linked_employee_including_rotations(self):
        self.create()
        self.client.force_login(self.worker)
        response = self.client.get(reverse("mi_horario_datos"), {"desde": "2026-09-07", "empleado_id": self.mapping["Cristian"]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["eventos"])
        self.assertEqual({event["empleado_id"] for event in response.json()["eventos"]}, {self.mapping["Camila"]})
        self.assertNotIn("rotaciones", response.json())
        self.assertNotIn("Jackeline", response.content.decode())

    def test_unlinked_user_gets_no_rotations_and_ordinary_user_cannot_import(self):
        self.create()
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("mi_horario_datos"), {"desde": "2026-09-07"}).json()["eventos"], [])
        with self.assertRaises(PermissionDenied):
            rotation.create_rotation(self.worker, self.payload())

    def test_api_import_and_scope_save(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("crear_rotacion_empleados"), data=json.dumps(self.payload()), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        cycle = RotacionEmpleado.objects.get()
        response = self.client.post(reverse("guardar_turno_empleado"), data=json.dumps(self.change(self.event(cycle), notas="Prueba API")), content_type="application/json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["evento"]["notas"], "Prueba API")

    def test_bot_queries_recurrent_shifts_and_requires_scope_before_proposal(self):
        cycle = self.create()
        reply = bot._execute_tool(self.worker_profile, "consultar_horarios_empleados", {"desde": "2026-09-07", "hasta": "2026-09-07"})
        self.assertIn("Camila", reply.text)
        self.assertIn("Semana 1/4", reply.text)
        self.assertNotIn("Cristian", reply.text)
        with self.assertRaises(bot.TelegramBotError):
            bot._execute_tool(self.profile, "preparar_turno_empleado", {"operacion": "editar", "turno_referencia": self.event(cycle)["id"], "notas": "Sin alcance"})
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_bot_future_change_requires_confirmation_and_revalidates(self):
        cycle = self.create()
        event = self.event(cycle)
        reply = bot._execute_tool(self.profile, "preparar_turno_empleado", {"operacion": "editar", "turno_referencia": event["id"], "alcance": "futuro", "notas": "Desde Telegram"})
        self.assertIn("SIGUIENTES REPETICIONES", reply.text)
        self.assertNotEqual(self.event(cycle)["notas"], "Desde Telegram")
        callback = SimpleNamespace(texto=reply.reply_markup["inline_keyboard"][0][0]["callback_data"], callback_query_id="rotation")
        self.assertIn("guardado correctamente", bot._handle_callback(callback, self.profile, self.client_stub).text)
        self.assertEqual(self.event(cycle, repeat=4)["notas"], "Desde Telegram")
        self.assertEqual(CambioRotacionEmpleado.objects.order_by("pk").last().origen, "TELEGRAM")

    def test_management_command_dry_run_and_apply_are_repeatable(self):
        call_command("cargar_rotacion_yerbabuena", usuario=self.admin.nombreusuario, stdout=StringIO())
        self.assertFalse(RotacionEmpleado.objects.exists())
        for _ in range(2):
            call_command("cargar_rotacion_yerbabuena", usuario=self.admin.nombreusuario, apply=True, stdout=StringIO())
        self.assertEqual(RotacionEmpleado.objects.count(), 1)

    def test_ambiguous_employee_names_are_not_chosen_automatically(self):
        Empleado.objects.bulk_create([Empleado(nombre="Cristian segundo", apellido="Otro", telefono="x", email="x@example.test", numerodocumento="x")])
        self.assertIsNone(rotation.suggested_mapping()["Cristian"])


class RotationMigrationTests(TransactionTestCase):
    def test_migration_creates_all_rotation_models(self):
        migration = importlib.import_module("mainApp.migrations.0038_employee_rotation").Migration("0038_employee_rotation", "mainApp")
        models = (CambioRotacionEmpleado, MiembroRotacionEmpleado, RotacionEmpleado)
        state = ProjectState.from_apps(apps)
        for model in models:
            state.remove_model("mainApp", model._meta.model_name)
        with connection.schema_editor() as editor:
            for model in models:
                editor.delete_model(model)
            result = migration.apply(state, editor)
        for model in models:
            actual, expected = result.models["mainApp", model._meta.model_name], ModelState.from_model(model)
            self.assertEqual({key: value.deconstruct()[1:] for key, value in actual.fields.items()}, {key: value.deconstruct()[1:] for key, value in expected.fields.items()})
            self.assertEqual(actual.options, expected.options)
