"""Approved October schedule, with a dated replacement and no production data."""
import json
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from mainApp.models import CambioRotacionEmpleado, Empleado, Rol, RotacionEmpleado, Sucursal, TurnoEmpleado, Usuario
from mainApp.services import employee_rotation as rotation
from mainApp.services import employee_schedule as schedule


class OctoberRotationTests(TestCase):
    def setUp(self):
        today = patch.object(rotation.timezone, "localdate", return_value=date(2026, 10, 20))
        today.start()
        self.addCleanup(today.stop)
        self.admin = Usuario.objects.create_user("Responsable prueba", rolid=Rol.objects.create(nombre="Web Master"))
        self.worker = Usuario.objects.create_user("Cajero prueba")
        self.branch = Sucursal.objects.create(nombre="Yerbabuena")
        self.legacy = json.loads((Path(__file__).parent / "data/employee_rotation_four_weeks.json").read_text(encoding="utf-8"))
        people = sorted(set(rotation.preset()["personas"]) | set(self.legacy["personas"]))
        Empleado.objects.bulk_create([
            Empleado(nombre=name, apellido="Prueba", telefono=str(index), email=f"{index}@example.test",
                     numerodocumento=str(index), sucursalid=self.branch, usuarioid=self.worker if name == "Fabián" else None)
            for index, name in enumerate(people)
        ])
        self.mapping = rotation.suggested_mapping(self.branch.pk)

    def payload(self, **changes):
        return dict(inicio="2026-10-26", empleados=self.mapping, sucursal_id=self.branch.pk,
                    solicitud_id=str(uuid4()), **changes)

    def create(self, **changes):
        result, _ = rotation.create_rotation(self.admin, self.payload(**changes))
        return RotacionEmpleado.objects.get(pk=result["id"])

    def old_rotation(self):
        with patch.object(rotation, "preset", return_value=self.legacy):
            payload = dict(inicio="2026-09-07", empleados=rotation.suggested_mapping(),
                           sucursal_id=self.branch.pk, solicitud_id=str(uuid4()))
            result, _ = rotation.create_rotation(self.admin, payload)
        return RotacionEmpleado.objects.get(pk=result["id"])

    def events(self, start, end, **kwargs):
        return rotation.events_between(schedule.parse_time(start + "T00:00"), schedule.parse_time(end + "T00:00"), **kwargs)

    def test_all_28_days_exact_assignments_and_complete_rests(self):
        cycle = self.create()
        self.assertEqual(cycle.inicio, date(2026, 10, 26))
        self.assertEqual(len(cycle.patron), 300)
        self.assertEqual(sum(item["descanso"] for item in cycle.patron), 96)
        self.assertEqual(sum(item["duracion"] for item in cycle.patron if not item["descanso"]), 1288 * 60)
        # Independent transcription of the approved morning/afternoon tables.
        names = {"J": "Jackeline", "C": "Cristian", "B": "Bibiana", "M": "Camila", "R": "Cristina", "L": "Lina", "A": "Alejandra", "F": "Fabián"}
        expected = [
            [("JCB", "ML"), ("JCR", "BM"), ("JCB", "MR"), ("JCM", "BR"), ("JCR", "BL"), ("JBR", "CM"), ("AR", "FL")],
            [("JCM", "LR"), ("JCR", "MB"), ("JCM", "BR"), ("JCR", "BM"), ("JCB", "RL"), ("JBM", "CR"), ("AM", "LF")],
            [("JCR", "BL"), ("JCR", "BM"), ("JCB", "MR"), ("JCM", "BR"), ("JCM", "BL"), ("JCR", "BM"), ("AR", "LF")],
            [("JCB", "LR"), ("JCR", "MB"), ("JCB", "MR"), ("JCM", "BR"), ("JCR", "ML"), ("JCB", "MR"), ("AM", "BL")],
        ]
        for week, days in enumerate(expected, 1):
            for day, (morning, afternoon) in enumerate(days):
                with self.subTest(week=week, day=day):
                    slots = [item for item in cycle.patron if item["semana"] == week and item["dia"] == day]
                    for minute, letters in [(420, morning), (840, afternoon)]:
                        regular = [item for item in slots if not item["descanso"] and item["duracion"] == 420 and item["minuto"] == minute]
                        self.assertEqual({item["empleado_id"] for item in regular}, {self.mapping[names[letter]] for letter in letters})
                    special = {"Duvan" if day == 4 else "Claudia"}
                    if 1 <= day <= 5:
                        special.add("Fabián")
                        self.assertEqual(sorted((item["minuto"], item["duracion"]) for item in slots if item["empleado_id"] == self.mapping["Fabián"]), [(420, 300), (900, 240)])
                    working = {names[letter] for letter in morning + afternoon} | special
                    rest_ids = {self.mapping[name] for name in set(self.mapping) - working}
                    self.assertEqual({item["empleado_id"] for item in slots if item["descanso"]}, rest_ids)
                    self.assertEqual({item["empleado_id"] for item in slots}, set(self.mapping.values()))
        self.assertFalse(TurnoEmpleado.objects.exists())

    def test_nights_midnight_and_next_cycle(self):
        self.create()
        for repetition in [0, 1, 20]:
            monday = date(2026, 10, 26) + timedelta(days=28 * repetition)
            events = [event for event in self.events(monday.isoformat(), (monday + timedelta(days=7)).isoformat()) if not event["descanso"]]
            friday = (monday + timedelta(days=4)).isoformat()
            duvan = next(event for event in events if event["empleado_id"] == self.mapping["Duvan"])
            self.assertEqual(duvan["inicio"], friday + "T15:00:00-05:00")
            self.assertEqual(duvan["fin"], (monday + timedelta(days=5)).isoformat() + "T00:00:00-05:00")
            thursday = next(event for event in events if event["empleado_id"] == self.mapping["Claudia"] and event["fecha_base"] == (monday + timedelta(days=3)).isoformat())
            self.assertEqual(thursday["fin"], friday + "T01:00:00-05:00")
            self.assertTrue(all(event["semana_rotacion"] == 1 for event in events if event["fecha_base"] >= monday.isoformat()))

    def test_replacement_preserves_history_and_ends_all_old_members(self):
        old = self.old_rotation()
        before = [{key: value for key, value in event.items() if key != "version"} for event in self.events("2026-10-19", "2026-10-26")]
        self.create(reemplazar_id=old.pk, reemplazar_version=old.version)
        after = [{key: value for key, value in event.items() if key != "version"} for event in self.events("2026-10-19", "2026-10-26")]
        self.assertEqual(before, after)
        future = self.events("2026-10-26", "2026-11-24")
        self.assertFalse(any(event["rotacion_id"] == old.pk and event["fecha_base"] >= "2026-10-26" for event in future))
        # Sunday's night still ends at 01:00 on the Monday of the transition.
        tail = [event for event in future if event["rotacion_id"] == old.pk]
        self.assertEqual(len(tail), 1)
        self.assertEqual(tail[0]["fin"], "2026-10-26T01:00:00-05:00")
        old.refresh_from_db()
        self.assertEqual(rotation.metadata(old)["fin_exclusivo"], "2026-10-26")
        self.assertEqual(len(old.patron), 200)
        self.assertEqual(CambioRotacionEmpleado.objects.filter(alcance="finalizar").count(), 1)

    def test_no_implicit_replacement_and_atomic_rollback_on_conflict(self):
        old = self.old_rotation()
        with self.assertRaises(schedule.ScheduleConflict):
            self.create()
        TurnoEmpleado.objects.create(empleado_id=self.mapping["Lina"], sucursal=self.branch,
                                     inicio=schedule.parse_time("2027-02-15T14:00"), fin=schedule.parse_time("2027-02-15T15:00"))
        with self.assertRaises(schedule.ScheduleConflict):
            self.create(reemplazar_id=old.pk, reemplazar_version=old.version)
        old.refresh_from_db()
        self.assertEqual(old.version, 1)
        self.assertIsNone(rotation.end_date(old))
        self.assertEqual(RotacionEmpleado.objects.count(), 1)
        self.assertEqual(CambioRotacionEmpleado.objects.count(), 1)

    def test_replacement_idempotency_stale_version_and_branch_checks(self):
        old = self.old_rotation()
        with self.assertRaises(schedule.ScheduleConflict):
            self.create(reemplazar_id=old.pk, reemplazar_version=old.version + 1)
        other_branch = Sucursal.objects.create(nombre="Otra")
        payload = self.payload(reemplazar_id=old.pk, reemplazar_version=old.version)
        with self.assertRaises(schedule.ScheduleError):
            rotation.create_rotation(self.admin, dict(payload, sucursal_id=other_branch.pk))
        result = rotation.create_rotation(self.admin, payload)
        self.assertEqual(rotation.create_rotation(self.admin, payload), (result[0], False))
        self.assertEqual(RotacionEmpleado.objects.count(), 2)
        self.assertEqual(CambioRotacionEmpleado.objects.count(), 3)

    def test_old_future_exceptions_cannot_reappear_after_replacement(self):
        old = self.old_rotation()
        slot = next(item for item in old.patron if item["semana"] == 4 and item["dia"] == 0 and item["empleado_id"] == self.mapping["Cristian"])
        ref = f"r{old.pk}-{slot['clave']}-20261026"
        rotation.save_occurrence(self.admin, dict(id=ref, operacion="editar", version=1, alcance="fecha",
                                                 notas="Excepción antigua futura", solicitud_id=str(uuid4())))
        old.refresh_from_db()
        self.create(reemplazar_id=old.pk, reemplazar_version=old.version)
        self.assertTrue(rotation.occurrence(ref)[3]["cancelado"])
        self.assertFalse(any(event["notas"] == "Excepción antigua futura" for event in self.events("2026-10-26", "2026-10-27")))

    def test_new_work_on_day_off_still_conflicts_and_split_gap_is_free(self):
        self.create()
        with self.assertRaises(schedule.ScheduleConflict):
            rotation.assert_free(self.mapping["Claudia"], schedule.parse_time("2026-10-30T08:00"), schedule.parse_time("2026-10-30T09:00"))
        rotation.assert_free(self.mapping["Fabián"], schedule.parse_time("2026-10-27T12:00"), schedule.parse_time("2026-10-27T15:00"))

    def test_permissions_personal_api_and_management_dry_run(self):
        with self.assertRaises(PermissionDenied):
            rotation.create_rotation(self.worker, self.payload())
        call_command("cargar_rotacion_yerbabuena", usuario=self.admin.nombreusuario, stdout=StringIO())
        self.assertFalse(RotacionEmpleado.objects.exists())
        self.create()
        self.client.force_login(self.worker)
        data = self.client.get(reverse("mi_horario_datos"), {"desde": "2026-10-27", "hasta": "2026-10-27"}).json()
        self.assertEqual(len(data["eventos"]), 2)
        self.assertEqual({event["empleado_id"] for event in data["eventos"]}, {self.mapping["Fabián"]})
        self.assertEqual([event["inicio"][11:16] for event in data["eventos"]], ["07:00", "15:00"])

    def test_replacement_command_simulation_and_repeated_apply(self):
        old = self.old_rotation()
        options = dict(usuario=self.admin.nombreusuario, reemplazar=old.pk, version_anterior=old.version, stdout=StringIO())
        call_command("cargar_rotacion_yerbabuena", **options)
        self.assertEqual(RotacionEmpleado.objects.count(), 1)
        self.assertIsNone(rotation.end_date(old))
        for _ in range(2):
            call_command("cargar_rotacion_yerbabuena", apply=True, **options)
        self.assertEqual(RotacionEmpleado.objects.count(), 2)
