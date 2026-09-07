import importlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.apps import apps
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.migrations.state import ModelState, ProjectState
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from mainApp.models import (
    CambioTurnoEmpleado, Empleado, Permiso, Rol, Sucursal, TelegramAccionPendiente,
    TelegramUsuario, TurnoCaja, TurnoEmpleado, Usuario, UsuarioPermiso,
)
from mainApp.permissions import clear_permission_cache, user_can_access_url_name
from mainApp.services import employee_schedule as schedule
from mainApp.services import telegram_bot as bot
from mainApp.services import telegram_assistant as assistant


@override_settings(TIME_ZONE="America/Bogota", USE_TZ=True)
class ScheduleTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = Usuario.objects.create_user("Administrador calendario", rolid=Rol.objects.create(nombre="Web Master"))
        # Sin rol: las consultas propias no requieren permisos de administración.
        self.worker = Usuario.objects.create_user("Ana calendario")
        self.other_user = Usuario.objects.create_user("Luis calendario")
        self.branch = Sucursal.objects.create(nombre="Centro calendario")
        self.branch2 = Sucursal.objects.create(nombre="Norte calendario")
        Empleado.objects.bulk_create([
            Empleado(nombre="Ana", apellido="Perez", usuarioid=self.worker, sucursalid=self.branch, numerodocumento="100", email="ana@example.test", telefono="100"),
            Empleado(nombre="Luis", apellido="Gomez", usuarioid=self.other_user, sucursalid=self.branch, numerodocumento="200", email="luis@example.test", telefono="200"),
        ])
        self.ana = Empleado.objects.get(usuarioid=self.worker)
        self.luis = Empleado.objects.get(usuarioid=self.other_user)
        self.profile = TelegramUsuario.objects.create(usuario=self.admin, telegram_user_id=990, telegram_chat_id=990)
        self.worker_profile = TelegramUsuario.objects.create(usuario=self.worker, telegram_user_id=991, telegram_chat_id=991)
        self.stub = SimpleNamespace(answer_callback=MagicMock())

    def payload(self, **overrides):
        return dict({"operacion": "crear", "empleado_id": self.ana.pk, "sucursal_id": self.branch.pk,
                     "inicio": "2026-09-08T08:00", "fin": "2026-09-08T17:00", "notas": "Apertura",
                     "solicitud_id": str(uuid4())}, **overrides)

    def create(self, **overrides):
        return schedule.save_change(self.admin, self.payload(**overrides))[0]

    def change(self, turn, **values):
        return dict({"operacion": "editar", "id": turn.pk, "version": turn.version, "solicitud_id": str(uuid4())}, **values)

    def query(self, profile=None, **arguments):
        return bot._execute_tool(profile or self.profile, "consultar_horarios_empleados", dict({"desde": "2026-09-08", "hasta": "2026-09-09"}, **arguments))

    def propose(self, **arguments):
        return bot._execute_tool(self.profile, "preparar_turno_empleado", dict({"operacion": "crear", "empleado": str(self.ana.pk), "inicio": "2026-09-08T08:00", "fin": "2026-09-08T17:00"}, **arguments))

    def callback(self, reply, profile=None, confirm=True):
        button = reply.reply_markup["inline_keyboard"][0][0 if confirm else 1]
        return bot._handle_callback(SimpleNamespace(texto=button["callback_data"], callback_query_id="schedule-test"), profile or self.profile, self.stub)

    def test_create_is_audited_and_does_not_open_cash(self):
        turn = self.create()
        self.assertEqual(turn.inicio, schedule.parse_time("2026-09-08T13:00+00:00"))
        self.assertEqual(turn.creado_por, self.admin)
        log = CambioTurnoEmpleado.objects.get()
        self.assertEqual((log.origen, log.operacion, log.usuario), ("WEB", "crear", self.admin))
        self.assertEqual(log.nuevo["empleado_id"], self.ana.pk)
        self.assertEqual(log.anterior, {})
        self.assertFalse(TurnoCaja.objects.exists())

    def test_duplicate_retry_is_idempotent_and_token_cannot_change_payload(self):
        payload = self.payload()
        first, saved = schedule.save_change(self.admin, payload)
        second, saved_again = schedule.save_change(self.admin, payload)
        self.assertEqual(first.pk, second.pk)
        self.assertTrue(saved)
        self.assertFalse(saved_again)
        self.assertEqual(CambioTurnoEmpleado.objects.count(), 1)
        with self.assertRaises(schedule.ScheduleConflict):
            schedule.save_change(self.admin, dict(payload, notas="Otra petición"))

    def test_conflicting_intervals_across_branches_are_rejected(self):
        self.create()
        with self.assertRaises(schedule.ScheduleConflict):
            self.create(inicio="2026-09-08T16:00", fin="2026-09-08T20:00", sucursal_id=self.branch2.pk)
        self.assertEqual(TurnoEmpleado.objects.count(), 1)

    def test_adjacent_turns_and_other_employee_same_time_are_allowed(self):
        self.create()
        self.create(inicio="2026-09-08T17:00", fin="2026-09-08T19:00")
        self.create(empleado_id=self.luis.pk)
        self.assertEqual(TurnoEmpleado.objects.count(), 3)

    def test_night_turn_included_on_both_dates(self):
        self.create(inicio="2026-09-08T22:00", fin="2026-09-09T06:00")
        for day in ("2026-09-08", "2026-09-09"):
            self.assertEqual(schedule.schedule_rows(self.worker, {"desde": day, "hasta": day}, personal=True).count(), 1)

    def test_invalid_payloads_leave_no_rows(self):
        invalid = [{"fin": "2026-09-08T07:00"}, {"fin": "2026-09-09T09:00"}, {"inicio": "bad"},
                   {"inicio": "2026-09-08T08:00:01"}, {"notas": "x" * 301}, {"empleado_id": True},
                   {"solicitud_id": "bad"}, {"version": 1}, {"precio": 0}, {"empleado_id": 99999}]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(schedule.ScheduleError):
                self.create(**fields)
        self.assertFalse(TurnoEmpleado.objects.exists())

    def test_edit_version_and_stale_conflict(self):
        turn = self.create()
        payload = self.change(turn, inicio="2026-09-09T08:00", fin="2026-09-09T17:00")
        updated, _ = schedule.save_change(self.admin, payload)
        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.inicio.day, 9)
        with self.assertRaises(schedule.ScheduleConflict):
            schedule.save_change(self.admin, self.change(turn, notas="Cambio desactualizado"))
        self.assertEqual(CambioTurnoEmpleado.objects.count(), 2)

    def test_cancellation_preserves_history_and_frees_slot(self):
        turn = self.create()
        updated, _ = schedule.save_change(self.admin, self.change(turn, operacion="cancelar"))
        self.assertTrue(updated.cancelado)
        self.assertFalse(schedule.schedule_rows(self.worker, {"desde": "2026-09-08"}, personal=True).exists())
        self.create()
        self.assertEqual(TurnoEmpleado.objects.count(), 2)
        self.assertEqual(CambioTurnoEmpleado.objects.count(), 3)

    def test_employee_cannot_write_or_query_other_employee(self):
        with self.assertRaises(PermissionDenied):
            schedule.save_change(self.worker, self.payload())
        with self.assertRaises(PermissionDenied):
            schedule.schedule_rows(self.worker, {"empleado_id": self.luis.pk}, personal=True)

    def test_manage_permission_implies_calendar_and_read_permission_cannot_write(self):
        permission = Permiso.objects.create(nombre="Ver calendario de empleados")
        UsuarioPermiso.objects.create(usuario=self.worker, permiso=permission, permitido=True)
        clear_permission_cache(self.worker)
        self.assertTrue(user_can_access_url_name(self.worker, "calendario_empleados"))
        self.assertFalse(user_can_access_url_name(self.worker, "guardar_turno_empleado"))
        permission = Permiso.objects.create(nombre="Gestionar turnos de empleados")
        UsuarioPermiso.objects.create(usuario=self.other_user, permiso=permission, permitido=True)
        clear_permission_cache(self.other_user)
        self.assertTrue(user_can_access_url_name(self.other_user, "calendario_empleados"))
        self.assertTrue(user_can_access_url_name(self.other_user, "guardar_turno_empleado"))

    def test_personal_api_ignores_forged_employee_filter(self):
        self.create()
        self.create(empleado_id=self.luis.pk, notas="Luis privado")
        self.client.force_login(self.worker)
        response = self.client.get(reverse("mi_horario_datos"), {"desde": "2026-09-08", "empleado_id": self.luis.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["empleado_id"] for row in response.json()["eventos"]], [self.ana.pk])
        self.assertNotContains(response, "Luis privado")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_own_page_has_no_employee_catalog_and_requires_login(self):
        self.assertEqual(self.client.get(reverse("mi_horario")).status_code, 302)
        self.client.force_login(self.worker)
        response = self.client.get(reverse("mi_horario"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["calendar_config"]["editable"])
        self.assertEqual(response.context["calendar_config"]["empleados"], [])
        self.assertNotContains(response, "Luis Gomez")
        self.assertContains(response, "calendario_empleados.js")

    def test_unlinked_personal_page_shows_notice_and_no_events(self):
        self.create()
        self.client.force_login(self.admin)
        response = self.client.get(reverse("mi_horario"))
        self.assertContains(response, "Tu usuario aún no está vinculado")
        response = self.client.get(reverse("mi_horario_datos"), {"desde": "2026-09-08"})
        self.assertEqual(response.json()["eventos"], [])

    def test_web_admin_save_uses_lazy_user_and_retries(self):
        self.client.force_login(self.admin)
        payload = self.payload()
        for expected in (True, False):
            response = self.client.post(reverse("guardar_turno_empleado"), data=json.dumps(payload), content_type="application/json")
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["guardado"], expected)

    def test_web_rejects_csrf_and_unauthorized_edit(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(reverse("guardar_turno_empleado"), data=json.dumps(self.payload()), content_type="application/json").status_code, 403)
        self.client.force_login(self.worker)
        response = self.client.post(reverse("guardar_turno_empleado"), data=json.dumps(self.payload()), content_type="application/json", HTTP_ACCEPT="application/json")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(TurnoEmpleado.objects.exists())

    def test_web_accepts_valid_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.get(reverse("calendario_empleados")).status_code, 200)
        response = client.post(reverse("guardar_turno_empleado"), data=json.dumps(self.payload()), content_type="application/json", HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value)
        self.assertEqual(response.status_code, 200)

    def test_web_invalid_json_and_interval(self):
        self.client.force_login(self.admin)
        for data in ("[]", "not-json", '{"operacion":"crear"}'):
            response = self.client.post(reverse("guardar_turno_empleado"), data=data, content_type="application/json")
            self.assertEqual(response.status_code, 400)
        for args in ({"desde": "2026-09-08", "hasta": "2026-01-01"}, {"desde": "2026-01-01", "hasta": "2026-12-31"}):
            self.assertEqual(self.client.get(reverse("calendario_empleados_datos"), args).status_code, 400)

    def test_database_enforces_positive_interval(self):
        turn = self.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            TurnoEmpleado.objects.filter(pk=turn.pk).update(fin=turn.inicio)

    def test_bot_queries_own_or_all_with_permissions(self):
        self.create()
        self.create(empleado_id=self.luis.pk)
        reply = self.query(self.worker_profile)
        self.assertIn("Ana Perez", reply.text)
        self.assertNotIn("Luis Gomez", reply.text)
        self.assertIn("Luis Gomez", self.query(todos=True).text)
        self.assertIn("Luis Gomez", self.query(empleado=str(self.luis.pk)).text)
        with self.assertRaises(PermissionDenied):
            self.query(self.worker_profile, todos=True)
        with self.assertRaises(PermissionDenied):
            self.query(self.worker_profile, empleado="Luis")

    def test_bot_pagination_and_filtered_continuation(self):
        for hour in range(8, 14):
            self.create(inicio=f"2026-09-08T{hour:02}:00", fin=f"2026-09-08T{hour+1:02}:00")
        reply = self.query(todos=True)
        self.assertEqual(reply.pagination["pages"], 2)
        self.assertIn("Siguiente", str(reply.reply_markup))
        following = bot._execute_tool(self.profile, "continuar_consulta", {"navegacion": "siguiente"})
        self.assertIn("página 2/2", following.text)
        self.assertEqual(following.pagination["arguments"]["desde"], "2026-09-08")

    def test_bot_creation_waits_for_confirmation_and_is_idempotent(self):
        reply = self.propose()
        self.assertFalse(TurnoEmpleado.objects.exists())
        self.assertIn(self.branch.nombre, reply.text)
        confirmed = self.callback(reply)
        self.assertIn("guardado", confirmed.text)
        self.callback(reply)
        self.assertEqual(TurnoEmpleado.objects.count(), 1)
        self.assertEqual(CambioTurnoEmpleado.objects.get().origen, "TELEGRAM")
        self.assertFalse(TurnoCaja.objects.exists())

    def test_bot_overlap_rechecked_after_proposal(self):
        reply = self.propose()
        self.create()
        self.assertIn("No se cambió", self.callback(reply).text)
        self.assertEqual(TelegramAccionPendiente.objects.get().estado, "ERROR")
        self.assertEqual(TurnoEmpleado.objects.count(), 1)

    def test_bot_audit_failure_rolls_back_shift_without_exposing_sql(self):
        reply = self.propose()
        with patch.object(CambioTurnoEmpleado.objects, "create", side_effect=DatabaseError("private SQL details")):
            result = self.callback(reply)
        self.assertIn("No se cambió", result.text)
        self.assertNotIn("SQL", result.text)
        self.assertFalse(TurnoEmpleado.objects.exists())
        self.assertEqual(TelegramAccionPendiente.objects.get().estado, "ERROR")

    def test_bot_edit_stale_proposal_not_overwritten(self):
        turn = self.create()
        reply = bot._execute_tool(self.profile, "preparar_turno_empleado", {"operacion": "editar", "turno_id": turn.pk, "notas": "Nota desde Telegram"})
        schedule.save_change(self.admin, self.change(turn, notas="Cambio web"))
        self.assertIn("No se cambió", self.callback(reply).text)
        turn.refresh_from_db()
        self.assertEqual(turn.notas, "Cambio web")

    def test_bot_cancel_shift_and_discard_proposal_are_distinct(self):
        turn = self.create()
        reply = bot._execute_tool(self.profile, "preparar_turno_empleado", {"operacion": "cancelar", "turno_id": turn.pk})
        self.callback(reply, confirm=False)
        turn.refresh_from_db()
        self.assertFalse(turn.cancelado)
        reply = bot._execute_tool(self.profile, "preparar_turno_empleado", {"operacion": "cancelar", "turno_id": turn.pk})
        self.assertIn("quedó cancelado", self.callback(reply).text)
        turn.refresh_from_db()
        self.assertTrue(turn.cancelado)

    def test_bot_proposal_ownership_expiry_and_permission_revocation(self):
        reply = self.propose()
        self.assertIn("pertenece a otra cuenta", self.callback(reply, profile=self.worker_profile).text)
        action = TelegramAccionPendiente.objects.get()
        action.vence_en -= timedelta(hours=1)
        action.save(update_fields=["vence_en"])
        self.assertIn("pasó el tiempo para confirmar", self.callback(reply).text)
        reply = self.propose()
        with patch("mainApp.services.employee_schedule.user_can_access_url_name", return_value=False):
            with self.assertRaises(PermissionDenied):
                self.callback(reply)
        self.assertFalse(TurnoEmpleado.objects.exists())

    def test_bot_denies_employee_writes_and_unknown_parameters(self):
        with self.assertRaises(PermissionDenied):
            bot._execute_tool(self.worker_profile, "preparar_turno_empleado", {"operacion": "crear"})
        for args in ({"operacion": "crear", "sql": "delete"}, {"operacion": "editar", "turno_id": True}, {"operacion": "crear", "inicio": "bad"}):
            with self.subTest(args=args), self.assertRaises(bot.TelegramBotError):
                bot._execute_tool(self.profile, "preparar_turno_empleado", args)
        self.assertFalse(TelegramAccionPendiente.objects.exists())

    def test_shortcuts_and_both_ai_providers_register_schedule_tools(self):
        self.assertEqual(assistant.common_read_request("Jarvis, muéstrame mi horario"), ("consultar_horarios_empleados", {}))
        name, args = assistant.common_read_request("Mi horario esta semana")
        self.assertEqual(name, "consultar_horarios_empleados")
        start, end, _, _ = schedule.date_window(args)
        self.assertEqual((start.weekday(), end.weekday(), (end-start).days), (0, 6, 6))
        declarations = bot.GEMINI_TOOLS[0]["functionDeclarations"]
        for name in ("consultar_horarios_empleados", "preparar_turno_empleado"):
            self.assertIn(name, {item["name"] for item in declarations})
            self.assertIn(name, {item["function"]["name"] for item in bot.GROQ_CHAT_TOOLS})
        with patch.object(bot, "_execute_tool", return_value=bot.BotReply("ok")) as execute:
            bot._handle_command(None, self.worker_profile, "/horario")
            execute.assert_called_with(self.worker_profile, "consultar_horarios_empleados", {}, None)


class ScheduleMigrationTests(TransactionTestCase):
    def test_migration_creates_schema_and_matches_new_models(self):
        module = importlib.import_module("mainApp.migrations.0037_employee_schedule")
        migration = module.Migration("0037_employee_schedule", "mainApp")
        state = ProjectState.from_apps(apps)
        state.remove_model("mainApp", "cambioturnoempleado")
        state.remove_model("mainApp", "turnoempleado")
        with connection.schema_editor() as editor:
            editor.delete_model(CambioTurnoEmpleado)
            editor.delete_model(TurnoEmpleado)
            result = migration.apply(state, editor)
        for model in (TurnoEmpleado, CambioTurnoEmpleado):
            expected = ModelState.from_model(model)
            actual = result.models["mainApp", model._meta.model_name]
            self.assertEqual({key: value.deconstruct()[1:] for key, value in actual.fields.items()}, {key: value.deconstruct()[1:] for key, value in expected.fields.items()})
            self.assertEqual(actual.options, expected.options)
            self.assertIn(model._meta.db_table, connection.introspection.table_names())
        self.assertTrue(Permiso.objects.filter(nombre="Gestionar turnos de empleados").exists())
