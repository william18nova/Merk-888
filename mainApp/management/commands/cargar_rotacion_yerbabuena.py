"""Importación explícita, validada y repetible de las cuatro tablas aprobadas."""
import json
from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import PermissionDenied
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, transaction

from mainApp.models import Empleado, Sucursal, Usuario
from mainApp.services import employee_rotation as rotation
from mainApp.services.employee_schedule import ScheduleError, require_access


class Command(BaseCommand):
    help = "Carga las 4 semanas de Yerbabuena. Simula por defecto; permite reemplazo explícito desde una fecha, conservando el historial."

    def add_arguments(self, parser):
        parser.add_argument("--usuario", required=True, help="Nombre exacto del usuario responsable con permiso para gestionar horarios.")
        parser.add_argument("--inicio", default=rotation.preset()["inicio_sugerido"], help="Lunes de inicio de la semana 1, YYYY-MM-DD.")
        parser.add_argument("--sucursal", default="Yerbabuena", help="Nombre exacto de la sucursal.")
        parser.add_argument("--apply", action="store_true", help="Guardar después de revisar la simulación.")
        parser.add_argument("--reemplazar", type=int, help="ID exacto de la rotación anterior de esta sucursal.")
        parser.add_argument("--version-anterior", type=int, help="Versión revisada de la rotación anterior. Obligatoria al reemplazar.")

    def handle(self, *args, **options):
        try:
            users = list(Usuario.objects.filter(nombreusuario__iexact=options["usuario"])[:2])
            branches = list(Sucursal.objects.filter(nombre__iexact=options["sucursal"])[:2])
            if len(users) != 1 or len(branches) != 1:
                raise CommandError("Indica un usuario y una sucursal existentes con nombres exactos y únicos.")
            user, branch = users[0], branches[0]
            require_access(user, write=True)
            mapping = rotation.suggested_mapping(branch.pk)
            missing = [name for name, value in mapping.items() if value is None]
            if missing:
                raise CommandError("No se asignaron nombres ambiguos o inexistentes: " + ", ".join(missing) + ". Relaciónalos desde la página del calendario.")
            employees = Empleado.objects.in_bulk(mapping.values())
            self.stdout.write(f"Sucursal: {branch.nombre} | Inicio: {options['inicio']} | Ciclo: 1 > 2 > 3 > 4 > 1")
            for name, employee_id in mapping.items():
                self.stdout.write(f"- {name} -> #{employee_id} {employees[employee_id]}")
            payload = {"inicio": options["inicio"], "nombre": rotation.preset()["nombre"], "sucursal_id": branch.pk, "empleados": mapping}
            if options["reemplazar"] is not None or options["version_anterior"] is not None:
                if options["reemplazar"] is None or options["version_anterior"] is None:
                    raise CommandError("Indica juntos --reemplazar y --version-anterior; no se elige una rotación automáticamente.")
                payload.update(reemplazar_id=options["reemplazar"], reemplazar_version=options["version_anterior"])
                self.stdout.write(f"Reemplaza rotación #{options['reemplazar']} versión {options['version_anterior']} desde {options['inicio']}; conserva el historial y sus excepciones anteriores.")
            slots = rotation._preset_slots(mapping, branch.pk)
            self.stdout.write(f"Por ciclo: {sum(not item['descanso'] for item in slots)} bloques de trabajo y {sum(item['descanso'] for item in slots)} descansos.")
            identity = json.dumps({"payload": payload, "preset": rotation.preset()}, sort_keys=True, ensure_ascii=True)
            payload["solicitud_id"] = str(uuid5(NAMESPACE_URL, "nova:employee-rotation:" + identity))
            with transaction.atomic():
                result, created = rotation.create_rotation(user, payload)
                if not options["apply"]:
                    transaction.set_rollback(True)
            if options["apply"]:
                self.stdout.write(self.style.SUCCESS(f"Rotación #{result['id']} {'guardada' if created else 'ya estaba guardada; no se duplicó'}."))
            else:
                self.stdout.write(self.style.SUCCESS("SIMULACIÓN CORRECTA. No se guardó ningún cambio. Añade --apply para activar esta rotación."))
        except (ScheduleError, PermissionDenied) as exc:
            raise CommandError(str(exc)) from None
        except DatabaseError:
            raise CommandError("No se pudo completar la importación. Revisa la conexión y aplica la migración 0038. No se guardaron cambios parciales.") from None
