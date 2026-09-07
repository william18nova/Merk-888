import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .models import Empleado, Sucursal
from .permissions import user_can_access_url_name
from .services import employee_schedule as schedule
from .services import employee_rotation as rotation


@login_required
@never_cache
@ensure_csrf_cookie
@require_GET
def calendar_page(request, personal=False):
    if not personal:
        schedule.require_access(request.user)
    elif not request.user.is_active:
        raise PermissionDenied("Tu usuario está inactivo.")
    employee = schedule.own_employee(request.user) if personal else None
    editable = not personal and user_can_access_url_name(request.user, "guardar_turno_empleado")
    employees = Empleado.objects.select_related("sucursalid").order_by("nombre", "apellido") if not personal else []
    config = {
        "personal": personal, "editable": editable,
        "datosUrl": reverse("mi_horario_datos" if personal else "calendario_empleados_datos"),
        "guardarUrl": reverse("guardar_turno_empleado") if editable else "",
        "rotacionUrl": reverse("crear_rotacion_empleados") if editable else "",
        "plantilla": rotation.preset() if editable else None,
        "mapeoSugerido": rotation.suggested_mapping() if editable else {},
        "empleados": [{"id": emp.pk, "nombre": str(emp), "sucursal_id": emp.sucursalid_id} for emp in employees],
        "sucursales": list(Sucursal.objects.order_by("nombre").values("sucursalid", "nombre")) if not personal else [],
        "empleadoNombre": str(employee) if employee else "", "sinVinculo": personal and employee is None,
    }
    return render(request, "calendario_empleados.html", {"calendar_config": config, "personal": personal, "editable": editable, "employee": employee})


@login_required
@never_cache
@require_GET
def calendar_data(request, personal=False):
    try:
        if not personal:
            schedule.require_access(request.user)
        args = request.GET.dict()
        if personal:
            # Nunca confiar en un empleado recibido del navegador en esta vista.
            args.pop("empleado_id", None)
        args["incluir_cancelados"] = args.get("incluir_cancelados") == "1"
        events = rotation.calendar_events(request.user, args, personal=personal)
        if len(events) > 2000:
            return JsonResponse({"error": "Hay demasiados turnos; filtra por empleado o consulta un intervalo menor."}, status=400)
        result = {"eventos": events}
        if not personal:
            result["rotaciones"] = [rotation.metadata(item) for item in rotation.RotacionEmpleado.objects.all()]
        return JsonResponse(result)
    except schedule.ScheduleError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    except DatabaseError:
        return JsonResponse({"error": "No se pudo cargar el calendario. Revisa la conexión y que estén aplicadas las migraciones 0037 y 0038."}, status=503)


@login_required
@never_cache
@require_POST
def calendar_save(request):
    try:
        schedule.require_access(request.user, write=True)
        if len(request.body) > 16000:
            raise schedule.ScheduleError("La solicitud es demasiado grande.")
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise schedule.ScheduleError("La solicitud no contiene JSON válido.") from None
        turn, changed = schedule.save_change(request.user, payload)
        return JsonResponse({"evento": schedule.serialize(turn), "guardado": changed})
    except schedule.ScheduleError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    except DatabaseError:
        return JsonResponse({"error": "No fue posible guardar el turno. Actualiza el calendario antes de reintentar; revisa la conexión y las migraciones 0037 y 0038."}, status=503)


@login_required
@never_cache
@require_POST
def rotation_create(request):
    try:
        schedule.require_access(request.user, write=True)
        if len(request.body) > 16000:
            raise schedule.ScheduleError("La solicitud es demasiado grande.")
        try:
            payload = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise schedule.ScheduleError("La solicitud no contiene JSON válido.") from None
        result, created = rotation.create_rotation(request.user, payload)
        return JsonResponse({"rotacion": result, "guardado": created})
    except schedule.ScheduleError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)
    except PermissionDenied as exc:
        return JsonResponse({"error": str(exc)}, status=403)
    except DatabaseError:
        return JsonResponse({"error": "No se pudo guardar la rotación. Revisa la conexión y la migración 0038; no se guardaron cambios parciales."}, status=503)
