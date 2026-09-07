"""Reglas compartidas por calendario y Telegram, con control de concurrencia."""

from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from mainApp.models import CambioTurnoEmpleado, Empleado, Sucursal, TurnoEmpleado, Usuario
from mainApp.permissions import user_can_access_url_name


COLOMBIA = ZoneInfo("America/Bogota")


class ScheduleError(ValueError):
    status = 400


class ScheduleConflict(ScheduleError):
    status = 409


def require_access(user, *, write=False):
    route = "guardar_turno_empleado" if write else "calendario_empleados"
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        raise PermissionDenied("Tu usuario no está activo.")
    if not user_can_access_url_name(user, route):
        raise PermissionDenied("No tienes permiso para administrar los horarios de empleados.")


def can_view_all(user):
    return bool(getattr(user, "is_active", False) and user_can_access_url_name(user, "calendario_empleados"))


def own_employee(user):
    return Empleado.objects.filter(usuarioid=user).first()


def positive_id(value, label="ID"):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or not 0 < int(value) <= 2147483647:
        raise ScheduleError(f"Indica un {label} válido.")
    return int(value)


def parse_time(value):
    try:
        result = parse_datetime(str(value))
        if result is None:
            raise ValueError
        if timezone.is_naive(result):
            result = timezone.make_aware(result, COLOMBIA)
        result = result.astimezone(COLOMBIA)
        if result.year < 2000 or result.year > 2100 or result.second or result.microsecond:
            raise ValueError
        return result
    except (ValueError, TypeError, OverflowError):
        raise ScheduleError("Indica fecha y hora válidas al minuto, por ejemplo 2026-09-08T08:00 (hora Colombia).") from None


def date_window(args):
    today = timezone.localdate(timezone=COLOMBIA)
    try:
        start = parse_date(str(args.get("desde"))) if args.get("desde") else today
        end = parse_date(str(args.get("hasta"))) if args.get("hasta") else start + timedelta(days=6)
        if not start or not end or start.year < 2000 or end.year > 2100 or not 0 <= (end - start).days <= 92:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ScheduleError("Consulta un intervalo válido de hasta 93 días, con fechas YYYY-MM-DD.") from None
    return start, end, datetime.combine(start, time.min, COLOMBIA), datetime.combine(end + timedelta(days=1), time.min, COLOMBIA)


def serialize(turn):
    return {
        "id": turn.pk, "empleado_id": turn.empleado_id, "empleado": str(turn.empleado),
        "sucursal_id": turn.sucursal_id, "sucursal": turn.sucursal.nombre,
        "inicio": turn.inicio.astimezone(COLOMBIA).isoformat(),
        "fin": turn.fin.astimezone(COLOMBIA).isoformat(),
        "notas": turn.notas, "cancelado": turn.cancelado, "version": turn.version,
        "actualizado_por": getattr(turn.actualizado_por, "nombreusuario", "") or "",
        "actualizado_en": turn.actualizado_en.isoformat() if turn.actualizado_en else None,
    }


def schedule_rows(user, args, *, personal=False):
    if not getattr(user, "is_authenticated", False) or not getattr(user, "is_active", False):
        raise PermissionDenied("Tu usuario no está activo.")
    _, _, start, end = date_window(args)
    rows = TurnoEmpleado.objects.select_related("empleado", "sucursal", "actualizado_por").filter(inicio__lt=end, fin__gt=start)
    if personal or not can_view_all(user):
        employee = own_employee(user)
        if args.get("empleado_id") and (employee is None or positive_id(args["empleado_id"]) != employee.pk):
            raise PermissionDenied("Solo puedes consultar tu propio horario.")
        rows = rows.filter(empleado=employee) if employee else rows.none()
    elif args.get("empleado_id"):
        rows = rows.filter(empleado_id=positive_id(args["empleado_id"]))
    if args.get("sucursal_id"):
        rows = rows.filter(sucursal_id=positive_id(args["sucursal_id"]))
    if not args.get("incluir_cancelados"):
        rows = rows.filter(cancelado=False)
    return rows.order_by("inicio", "pk")


def _prepare(user, payload, *, lock=False):
    require_access(user, write=True)
    allowed = {"operacion", "id", "version", "empleado_id", "sucursal_id", "inicio", "fin", "notas", "solicitud_id"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ScheduleError("La solicitud contiene campos no permitidos.")
    operation = payload.get("operacion")
    if operation not in {"crear", "editar", "cancelar"}:
        raise ScheduleError("Elige crear, editar o cancelar un turno.")
    turn = None
    if operation != "crear":
        rows = TurnoEmpleado.objects.select_related("empleado", "sucursal")
        if lock:
            rows = rows.select_for_update(of=("self",))
        turn = rows.filter(pk=positive_id(payload.get("id"), "ID de turno")).first()
        if not turn:
            raise ScheduleError("No encontré ese turno.")
        if turn.version != positive_id(payload.get("version"), "número de versión"):
            raise ScheduleConflict("El turno cambió mientras lo revisabas. Actualiza el calendario o solicita una nueva propuesta.")
        if turn.cancelado:
            raise ScheduleConflict("Ese turno ya está cancelado.")
    elif payload.get("id") is not None or payload.get("version") is not None:
        raise ScheduleError("Un turno nuevo no debe tener ID ni versión.")
    before = serialize(turn) if turn else {}
    if operation == "cancelar":
        if set(payload) & {"empleado_id", "sucursal_id", "inicio", "fin", "notas"}:
            raise ScheduleError("Para cancelar no envíes cambios de empleado, horas o notas.")
        return turn, before, {}, operation
    employee_id = positive_id(payload.get("empleado_id", turn.empleado_id if turn else None), "ID de empleado")
    branch_id = positive_id(payload.get("sucursal_id", turn.sucursal_id if turn else None), "ID de sucursal")
    employee_ids = sorted({employee_id, *([turn.empleado_id] if turn else [])})
    employees = Empleado.objects.filter(pk__in=employee_ids).order_by("pk")
    # Serializa escrituras sobre el mismo empleado entre varias cajas/servidores.
    if lock:
        employees = employees.select_for_update()
    employees = {employee.pk: employee for employee in employees}
    if employee_id not in employees:
        raise ScheduleError("El empleado seleccionado ya no existe.")
    branch = Sucursal.objects.filter(pk=branch_id).first()
    if branch is None:
        raise ScheduleError("La sucursal seleccionada ya no existe.")
    start = parse_time(payload["inicio"]) if "inicio" in payload else (turn.inicio if turn else None)
    end = parse_time(payload["fin"]) if "fin" in payload else (turn.fin if turn else None)
    if start is None or end is None or not timedelta(0) < end - start <= timedelta(hours=24):
        raise ScheduleError("El fin debe ser posterior al inicio; cada turno puede durar hasta 24 horas. Para un turno nocturno usa la fecha siguiente al terminar.")
    notes = payload.get("notas", turn.notas if turn else "")
    if not isinstance(notes, str) or len(notes) > 300:
        raise ScheduleError("Las notas deben ser texto de máximo 300 caracteres.")
    overlap = TurnoEmpleado.objects.filter(empleado_id=employee_id, cancelado=False, inicio__lt=end, fin__gt=start)
    if turn:
        overlap = overlap.exclude(pk=turn.pk)
    conflict = overlap.first()
    if conflict:
        raise ScheduleConflict(f"{employees[employee_id]} ya tiene un turno que se cruza con ese horario (turno #{conflict.pk}).")
    values = {"empleado": employees[employee_id], "sucursal": branch, "inicio": start, "fin": end, "notas": notes.strip()}
    return turn, before, values, operation


def preview_change(user, payload):
    turn, before, values, operation = _prepare(user, payload)
    after = dict(before)
    if operation == "cancelar":
        after["cancelado"] = True
    else:
        after.update(empleado_id=values["empleado"].pk, empleado=str(values["empleado"]), sucursal_id=values["sucursal"].pk, sucursal=values["sucursal"].nombre, inicio=values["inicio"].astimezone(COLOMBIA).isoformat(), fin=values["fin"].astimezone(COLOMBIA).isoformat(), notas=values["notas"], cancelado=False)
    return before, after


@transaction.atomic
def save_change(user, payload, *, source="WEB"):
    require_access(user, write=True)
    if not isinstance(payload, dict) or source not in {"WEB", "TELEGRAM"}:
        raise ScheduleError("La solicitud no es válida.")
    try:
        request_id = UUID(str(payload.get("solicitud_id")))
    except (ValueError, TypeError, AttributeError):
        raise ScheduleError("Falta un identificador válido de solicitud. Vuelve a intentarlo desde el calendario.") from None
    # Serializa reintentos del mismo administrador; la auditoría UUID es única.
    Usuario.objects.select_for_update().get(pk=user.pk)
    previous = CambioTurnoEmpleado.objects.select_related("turno").filter(solicitud_id=request_id).first()
    if previous:
        if previous.usuario_id != user.pk or previous.peticion != payload:
            raise ScheduleConflict("El identificador de solicitud ya fue utilizado.")
        return previous.turno, False
    turn, before, values, operation = _prepare(user, payload, lock=True)
    if operation == "crear":
        turn = TurnoEmpleado(**values, creado_por=user, actualizado_por=user)
    else:
        if operation == "cancelar":
            turn.cancelado = True
        else:
            for key, value in values.items():
                setattr(turn, key, value)
        turn.version += 1
        turn.actualizado_por = user
    turn.save()
    CambioTurnoEmpleado.objects.create(turno=turn, usuario=user, usuario_nombre=user.nombreusuario, operacion=operation, origen=source, solicitud_id=request_id, peticion=payload, anterior=before, nuevo=serialize(turn))
    return turn, True
