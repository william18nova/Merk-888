"""Rotaciones semanales y excepciones auditadas, calculadas al consultar.

Las modificaciones de plantilla tienen vigencia por fecha base de ocurrencia.
No se generan filas infinitas, ni se alteran retrospectivamente otras semanas.
"""

import json
import re
import math
import unicodedata
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from mainApp.models import (
    CambioRotacionEmpleado, CambioTurnoEmpleado, Empleado, MiembroRotacionEmpleado,
    RotacionEmpleado, Sucursal, TurnoEmpleado, Usuario,
)
from . import employee_schedule as schedule

MAX_OFFSET_DAYS = 34


@lru_cache(maxsize=1)
def preset():
    return json.loads((Path(__file__).resolve().parents[1] / "data" / "employee_rotation_four_weeks.json").read_text(encoding="utf-8"))


def cycle_days(rotation):
    return 7 * max(slot["semana"] for slot in rotation.patron)


def suggested_mapping():
    def normalized(value):
        return "".join(char for char in unicodedata.normalize("NFKD", str(value).lower()) if not unicodedata.combining(char))
    employees = list(Empleado.objects.all())
    result = {}
    for name in preset()["personas"]:
        matches = [employee for employee in employees if normalized(name) in normalized(employee.nombre).split()]
        result[name] = matches[0].pk if len(matches) == 1 else None
    return result


def _date(raw):
    try:
        value = str(raw)
        if re.fullmatch(r"[0-9]{8}", value):
            value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
        result = parse_date(value)
        if result is None or not 2000 <= result.year <= 2100:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise schedule.ScheduleError("Indica una fecha válida YYYY-MM-DD.") from None


def _midnight(day):
    return datetime.combine(day, time.min, schedule.COLOMBIA)


def is_reference(value):
    return isinstance(value, str) and value.startswith("r")


def _reference(value):
    match = re.fullmatch(r"r([1-9][0-9]{0,9})-([1-9][0-9]{0,5})-([0-9]{8})", str(value))
    if not match:
        raise schedule.ScheduleError("La referencia de la jornada iterativa no es válida.")
    return int(match[1]), int(match[2]), _date(match[3])


def _request_id(payload):
    try:
        return UUID(str(payload.get("solicitud_id")))
    except (ValueError, TypeError, AttributeError):
        raise schedule.ScheduleError("Falta un identificador válido de solicitud.") from None


def _repeat(user, payload):
    uid = _request_id(payload)
    if CambioTurnoEmpleado.objects.filter(solicitud_id=uid).exists():
        raise schedule.ScheduleConflict("El identificador ya fue utilizado en otra operación.")
    previous = CambioRotacionEmpleado.objects.filter(solicitud_id=uid).first()
    if previous and (previous.usuario_id != user.pk or previous.peticion != payload):
        raise schedule.ScheduleConflict("El identificador ya fue utilizado en otra operación.")
    return previous


def _log(rotation, user, payload, before, after, *, key=0, base=None, scope="crear", data=None, source="WEB"):
    return CambioRotacionEmpleado.objects.create(
        rotacion=rotation, clave=key, fecha_base=base or rotation.inicio, alcance=scope,
        datos=data or {}, usuario=user, usuario_nombre=user.nombreusuario, origen=source,
        solicitud_id=_request_id(payload), peticion=payload, anterior=before, nuevo=after,
    )


def metadata(rotation):
    weeks = cycle_days(rotation) // 7
    today = timezone.localdate(timezone=schedule.COLOMBIA)
    current = ((today - rotation.inicio).days // 7) % weeks + 1 if today >= rotation.inicio else None
    return {"id": rotation.pk, "nombre": rotation.nombre, "inicio": rotation.inicio.isoformat(), "version": rotation.version, "semanas": weeks, "semana_actual": current}


def _loaded_rotations(employee_ids=None):
    rows = RotacionEmpleado.objects.all()
    if employee_ids is not None:
        rows = rows.filter(miembros__empleado_id__in=employee_ids).distinct()
    return rows.prefetch_related("miembros__empleado", "miembros__sucursal", "cambios")


def _event(rotation, slot, base, changes, members):
    data = dict(slot)
    future = [item for item in changes if item.alcance == "futuro" and item.fecha_base <= base]
    revision = max(future, key=lambda item: item.pk, default=None)
    if revision:
        data.update(revision.datos)
    exact = [item for item in changes if item.fecha_base == base and item.alcance in {"fecha", "futuro"}]
    latest_exact = max(exact, key=lambda item: item.pk, default=None)
    exceptional = bool(latest_exact and latest_exact.alcance == "fecha")
    if exceptional:
        data.update(latest_exact.datos)
        revision = latest_exact
    employee, branch = members[data["empleado_id"], data["sucursal_id"]]
    start = _midnight(base + timedelta(days=data.get("desplazamiento", 0))) + timedelta(minutes=data["minuto"])
    end = start + timedelta(minutes=data["duracion"])
    return {
        "id": f"r{rotation.pk}-{slot['clave']}-{base:%Y%m%d}", "empleado_id": employee.pk,
        "empleado": str(employee), "sucursal_id": branch.pk, "sucursal": branch.nombre,
        "inicio": start.isoformat(), "fin": end.isoformat(), "notas": data.get("notas", ""),
        "cancelado": data.get("cancelado", False), "descanso": data.get("descanso", False),
        "version": rotation.version, "rotacion_id": rotation.pk, "rotacion_nombre": rotation.nombre,
        "semana_rotacion": slot["semana"], "fecha_base": base.isoformat(), "excepcion": exceptional,
        "semanas_ciclo": cycle_days(rotation) // 7,
        "actualizado_por": revision.usuario_nombre if revision else getattr(rotation.creado_por, "nombreusuario", ""),
        "actualizado_en": (revision.creado_en if revision else rotation.creado_en).isoformat(),
    }


def _parts(rotation):
    members = {(member.empleado_id, member.sucursal_id): (member.empleado, member.sucursal) for member in rotation.miembros.all()}
    changes = defaultdict(list)
    for change in rotation.cambios.all():
        changes[change.clave].append(change)
    return members, changes


def occurrence(ref):
    rotation_id, key, base = _reference(ref)
    rotation = _loaded_rotations().filter(pk=rotation_id).first()
    if rotation is None:
        raise schedule.ScheduleError("No encontré esa rotación.")
    slot = next((item for item in rotation.patron if item["clave"] == key), None)
    if slot is None:
        raise schedule.ScheduleError("No encontré esa jornada en la plantilla.")
    first = rotation.inicio + timedelta(days=(slot["semana"] - 1) * 7 + slot["dia"])
    if base < first or (base - first).days % cycle_days(rotation) or base < _date(slot.get("desde", first.isoformat())):
        raise schedule.ScheduleError("La fecha no corresponde a una repetición de esa jornada.")
    members, changes = _parts(rotation)
    return rotation, slot, base, _event(rotation, slot, base, changes[key], members)


def events_between(start, end, *, employee_ids=None, include_cancelled=False):
    result = []
    first_day = start.astimezone(schedule.COLOMBIA).date() - timedelta(days=35)
    last_day = end.astimezone(schedule.COLOMBIA).date() + timedelta(days=35)
    for rotation in _loaded_rotations(employee_ids).select_related("creado_por"):
        period = cycle_days(rotation)
        members, changes = _parts(rotation)
        for slot in rotation.patron:
            first = rotation.inicio + timedelta(days=(slot["semana"] - 1) * 7 + slot["dia"])
            since = _date(slot.get("desde", first.isoformat()))
            base = first + timedelta(days=max(0, (first_day - first).days // period) * period)
            while base <= last_day:
                if base >= since:
                    event = _event(rotation, slot, base, changes[slot["clave"]], members)
                    if (include_cancelled or not event["cancelado"]) and (employee_ids is None or event["empleado_id"] in employee_ids):
                        if schedule.parse_time(event["inicio"]) < end and schedule.parse_time(event["fin"]) > start:
                            result.append(event)
                base += timedelta(days=period)
    return result


def calendar_events(user, args, *, personal=False):
    # schedule_rows conserva la autorización y los filtros de las jornadas manuales.
    rows = schedule.schedule_rows(user, args, personal=personal)
    result = [schedule.serialize(turn) for turn in rows]
    _, _, start, end = schedule.date_window(args)
    employee_ids = None
    if personal or not schedule.can_view_all(user):
        employee = schedule.own_employee(user)
        employee_ids = [employee.pk] if employee else []
    elif args.get("empleado_id"):
        employee_ids = [schedule.positive_id(args["empleado_id"])]
    result.extend(events_between(start, end, employee_ids=employee_ids, include_cancelled=args.get("incluir_cancelados", False)))
    if args.get("sucursal_id"):
        branch_id = schedule.positive_id(args["sucursal_id"])
        result = [event for event in result if event["sucursal_id"] == branch_id]
    return sorted(result, key=lambda item: (item["inicio"], str(item["id"])))


def _all_events(start, end, employee_ids):
    rows = TurnoEmpleado.objects.select_related("empleado", "sucursal", "actualizado_por").filter(empleado_id__in=employee_ids, cancelado=False, inicio__lt=end, fin__gt=start)
    return [schedule.serialize(turn) for turn in rows] + events_between(start, end, employee_ids=employee_ids)


def _check_events(events):
    previous = {}
    for event in sorted(events, key=lambda item: (item["inicio"], str(item["id"]))):
        other = previous.get(event["empleado_id"])
        if other and other["fin"] > event["inicio"]:
            raise schedule.ScheduleConflict(f"Cruce de horario para {event['empleado']} el {event['inicio'][:10]}: {other['id']} y {event['id']}. Corrige el turno o descanso antes de guardar.")
        previous[event["empleado_id"]] = event


def validate_future(employee_ids, since):
    """Revisa dos ciclos alrededor de cada frontera y todas las fechas puntuales.

    Entre fronteras se usa el mínimo común múltiplo de los períodos. Esto
    también conserva compatibilidad con patrones históricos de cinco semanas.
    """
    points = {since}
    rotation_ids = list(RotacionEmpleado.objects.filter(miembros__empleado_id__in=employee_ids).values_list("pk", flat=True).distinct())
    period = math.lcm(*(cycle_days(item) for item in RotacionEmpleado.objects.filter(pk__in=rotation_ids))) if rotation_ids else 28
    points.update(day for day in RotacionEmpleado.objects.filter(pk__in=rotation_ids).values_list("inicio", flat=True) if day >= since)
    points.update(CambioRotacionEmpleado.objects.filter(rotacion_id__in=rotation_ids, fecha_base__gte=since - timedelta(days=35)).values_list("fecha_base", flat=True))
    for start, end in TurnoEmpleado.objects.filter(empleado_id__in=employee_ids, cancelado=False, fin__gte=_midnight(since)).values_list("inicio", "fin"):
        points.update([start.astimezone(schedule.COLOMBIA).date(), end.astimezone(schedule.COLOMBIA).date()])
    # Los turnos nuevos añadidos a una rotación también cambian su vigencia.
    for pattern in RotacionEmpleado.objects.filter(pk__in=rotation_ids).values_list("patron", flat=True):
        points.update(_date(slot["desde"]) for slot in pattern if slot.get("desde") and _date(slot["desde"]) >= since)
    windows = []
    for point in sorted(points):
        left, right = max(date(2000, 1, 1), point - timedelta(days=MAX_OFFSET_DAYS + 1)), min(date(2100, 12, 31), point + timedelta(days=period * 2 + MAX_OFFSET_DAYS + 2))
        if windows and left <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(right, windows[-1][1]))
        else:
            windows.append((left, right))
    for left, right in windows:
        _check_events(_all_events(_midnight(left), _midnight(right), employee_ids))


def assert_free(employee_id, start, end):
    events = events_between(start, end, employee_ids=[employee_id])
    if events:
        raise schedule.ScheduleConflict(f"Ya existe un turno o descanso iterativo para {events[0]['empleado']} en ese intervalo ({events[0]['id']}). Edita esa jornada en el calendario.")


def _preset_slots(mapping, branch_id):
    slots = []
    for week_index, week in enumerate(preset()["semanas"], 1):
        for day, groups in enumerate(week):
            for shift, names in enumerate(groups):
                for name in names:
                    minute, duration = [(420, 420), (840, 420), ((900 if day == 4 else 1200), (540 if day == 4 else 300)), (0, 1440)][shift]
                    slots.append({"clave": len(slots) + 1, "semana": week_index, "dia": day, "empleado_id": mapping[name], "sucursal_id": branch_id,
                                  "minuto": minute, "duracion": duration, "desplazamiento": 0, "descanso": shift == 3, "cancelado": False,
                                  "notas": "Descanso" if shift == 3 else "Especial · 15:00–00:00" if shift == 2 and day == 4 else "Noche · 20:00–01:00" if shift == 2 else preset()["columnas"][shift]})
    return slots


@transaction.atomic
def create_rotation(user, payload):
    schedule.require_access(user, write=True)
    if not isinstance(payload, dict) or set(payload) - {"inicio", "nombre", "empleados", "sucursal_id", "solicitud_id"}:
        raise schedule.ScheduleError("Datos de rotación no válidos.")
    Usuario.objects.select_for_update().get(pk=user.pk)
    previous = _repeat(user, payload)
    if previous:
        return previous.nuevo, False
    start = _date(payload.get("inicio"))
    if start.weekday() != 0:
        raise schedule.ScheduleError("La semana 1 debe comenzar un lunes.")
    mapping = payload.get("empleados")
    if not isinstance(mapping, dict) or set(mapping) != set(preset()["personas"]):
        raise schedule.ScheduleError("Relaciona las nueve personas de las tablas con sus empleados reales.")
    mapping = {name: schedule.positive_id(value, "ID de empleado") for name, value in mapping.items()}
    if len(set(mapping.values())) != len(mapping):
        raise schedule.ScheduleError("Cada nombre de la plantilla debe corresponder a un empleado diferente.")
    employees = list(Empleado.objects.filter(pk__in=mapping.values()).order_by("pk").select_for_update())
    if len(employees) != len(preset()["personas"]):
        raise schedule.ScheduleError("Uno de los empleados seleccionados no existe.")
    branch = Sucursal.objects.filter(pk=schedule.positive_id(payload.get("sucursal_id"))).first()
    if branch is None:
        raise schedule.ScheduleError("Selecciona una sucursal existente.")
    name = payload.get("nombre", preset()["nombre"])
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise schedule.ScheduleError("El nombre de la rotación debe tener entre 1 y 120 caracteres.")
    rotation = RotacionEmpleado.objects.create(nombre=name.strip(), inicio=start, creado_por=user, patron=_preset_slots(mapping, branch.pk))
    MiembroRotacionEmpleado.objects.bulk_create([MiembroRotacionEmpleado(rotacion=rotation, empleado=employee, sucursal=branch) for employee in employees])
    validate_future(list(mapping.values()), start)
    result = metadata(rotation)
    _log(rotation, user, payload, {}, result)
    return result, True


def _scope(payload):
    scope = payload.get("alcance")
    if scope not in {"fecha", "futuro"}:
        raise schedule.ScheduleError("Elige el alcance: solo esta fecha o esta y las siguientes repeticiones de la misma jornada.")
    return scope


def prepare_occurrence(user, payload):
    schedule.require_access(user, write=True)
    allowed = {"operacion", "id", "version", "empleado_id", "sucursal_id", "inicio", "fin", "notas", "solicitud_id", "alcance", "descanso"}
    if not isinstance(payload, dict) or set(payload) - allowed or payload.get("operacion") not in {"editar", "cancelar"}:
        raise schedule.ScheduleError("La solicitud de jornada iterativa no es válida.")
    scope = _scope(payload)
    rotation, slot, base, before = occurrence(payload.get("id"))
    if schedule.positive_id(payload.get("version"), "versión") != rotation.version:
        raise schedule.ScheduleConflict("La rotación cambió. Actualiza el calendario o solicita una propuesta nueva.")
    if before["cancelado"]:
        raise schedule.ScheduleConflict("Esa jornada ya está cancelada.")
    if scope == "futuro" and base < timezone.localdate(timezone=schedule.COLOMBIA):
        raise schedule.ScheduleError("Para cambiar la plantilla a partir de ahora, selecciona una ocurrencia de hoy o futura. Para fechas pasadas utiliza solo esta fecha.")
    if payload["operacion"] == "cancelar":
        if set(payload) & {"empleado_id", "sucursal_id", "inicio", "fin", "notas", "descanso"}:
            raise schedule.ScheduleError("Para cancelar no envíes cambios de empleado ni horas.")
        after = dict(before, cancelado=True)
    else:
        after = dict(before)
        for key in ("empleado_id", "sucursal_id", "inicio", "fin", "notas", "descanso"):
            if key in payload:
                after[key] = payload[key]
    employee = Empleado.objects.filter(pk=schedule.positive_id(after["empleado_id"])).first()
    branch = Sucursal.objects.filter(pk=schedule.positive_id(after["sucursal_id"])).first()
    if employee is None or branch is None:
        raise schedule.ScheduleError("El empleado o la sucursal ya no existe.")
    start, end = schedule.parse_time(after["inicio"]), schedule.parse_time(after["fin"])
    if not isinstance(after.get("descanso"), bool) or not isinstance(after.get("notas"), str) or len(after["notas"]) > 300:
        raise schedule.ScheduleError("Descanso debe ser verdadero/falso y las notas de hasta 300 caracteres.")
    if after["descanso"] and (start.time() != time.min or end != start + timedelta(days=1)):
        raise schedule.ScheduleError("Un descanso ocupa el día completo: de 00:00 a 00:00 del día siguiente.")
    minutes = int((end - start).total_seconds() // 60)
    offset = (start.date() - base).days
    if not 0 < minutes <= 1440 or abs(offset) >= cycle_days(rotation):
        raise schedule.ScheduleError(f"La jornada debe durar hasta 24 horas y moverse menos de {cycle_days(rotation)} días respecto a su fecha base.")
    if scope == "futuro" and start.date() < timezone.localdate(timezone=schedule.COLOMBIA):
        raise schedule.ScheduleError("Una modificación de plantilla no puede comenzar en una fecha pasada.")
    after.update(empleado_id=employee.pk, empleado=str(employee), sucursal_id=branch.pk, sucursal=branch.nombre, inicio=start.isoformat(), fin=end.isoformat(), notas=after["notas"].strip())
    data = {"empleado_id": employee.pk, "sucursal_id": branch.pk, "minuto": start.hour * 60 + start.minute, "duracion": minutes,
            "desplazamiento": offset, "descanso": after["descanso"], "notas": after["notas"], "cancelado": after["cancelado"]}
    return rotation, slot, base, before, after, data


@transaction.atomic
def save_occurrence(user, payload, *, source="WEB"):
    schedule.require_access(user, write=True)
    if source not in {"WEB", "TELEGRAM"}:
        raise schedule.ScheduleError("Origen de cambio inválido.")
    Usuario.objects.select_for_update().get(pk=user.pk)
    previous = _repeat(user, payload)
    if previous:
        return previous.nuevo, False
    rotation_id, _, _ = _reference(payload.get("id"))
    if not RotacionEmpleado.objects.filter(pk=rotation_id).select_for_update().first():
        raise schedule.ScheduleError("La rotación ya no existe.")
    rotation, slot, base, before, after, data = prepare_occurrence(user, payload)
    employee_ids = sorted({before["empleado_id"], after["empleado_id"]})
    list(Empleado.objects.filter(pk__in=employee_ids).order_by("pk").select_for_update())
    MiembroRotacionEmpleado.objects.get_or_create(rotacion=rotation, empleado_id=after["empleado_id"], sucursal_id=after["sucursal_id"])
    rotation.version += 1
    rotation.save(update_fields=["version"])
    after.update(version=rotation.version, excepcion=payload["alcance"] == "fecha", actualizado_por=user.nombreusuario, actualizado_en=timezone.now().isoformat())
    _log(rotation, user, payload, before, after, key=slot["clave"], base=base, scope=payload["alcance"], data=data, source=source)
    if payload["alcance"] == "futuro":
        validate_future(employee_ids, base)
    else:
        left = min(schedule.parse_time(before["inicio"]), schedule.parse_time(after["inicio"]))
        right = max(schedule.parse_time(before["fin"]), schedule.parse_time(after["fin"]))
        _check_events(_all_events(left, right, employee_ids))
    return after, True
