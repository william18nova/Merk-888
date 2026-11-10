"""Monotonic, row-locked checkpoint. Never changes financial balances."""
import json
from decimal import Decimal
from uuid import uuid4

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_http_methods

from .close_payments import validate

DENOMINATIONS = {"b100000": 100000, "b50000": 50000, "b20000": 20000,
    "b10000": 10000, "b5000": 5000, "b2000": 2000, "m1000": 1000,
    "m500": 500, "m200": 200, "m100": 100, "m50": 50,
    "pack_monedas": 10000, "pack_m50": 2000}


def normalize(data):
    if not isinstance(data, dict) or data.get("step") not in {"cash", "media"}:
        raise ValueError("El paso del cierre no es válido.")
    total, details = validate(data.get("invoices"), data.get("payment_details"))
    result = {"step": data["step"], "invoices": str(total), "payment_details": details, "denominations": None}
    if result["step"] == "media":
        counts = data.get("denominations")
        if not isinstance(counts, dict) or set(counts) != set(DENOMINATIONS):
            raise ValueError("El conteo de billetes y monedas está incompleto.")
        if any(type(n) is not int or not 0 <= n <= 1000000 for n in counts.values()):
            raise ValueError("Las cantidades deben ser enteras entre 0 y 1000000.")
        result["denominations"] = counts
    return result


def compatible(previous, new):
    return (previous["invoices"] == new["invoices"]
        and previous["payment_details"] == new["payment_details"]
        and (previous["step"] != "media" or previous["denominations"] == new["denominations"]))


def _record(turn):
    from mainApp.models import AvanceCierreCaja
    return AvanceCierreCaja.objects.filter(turno=turn).first()


def _attempt(record):
    return record.progreso.get("attempt", "") if record else ""


def _progress(record):
    if not record or "step" not in record.progreso:
        return None
    return {key: value for key, value in record.progreso.items() if key != "attempt"}


def attempt_id(turn):
    return _attempt(_record(turn))


def confirmed_progress(turn):
    return _progress(_record(turn))


def restart(turn, actor):
    """Replace the checkpoint, including its identity, under the turn lock."""
    from mainApp.models import AvanceCierreCaja
    AvanceCierreCaja.objects.update_or_create(turno=turn,
        defaults={"progreso": {"attempt": uuid4().hex}, "registrado_por": actor})


def align_local_attempt(turn_id, attempt, actor_id):
    """Copy the active identity to a local POS without erasing same-attempt drafts."""
    from mainApp.models import AvanceCierreCaja
    records = AvanceCierreCaja.objects.using("offline")
    record = records.filter(turno_id=turn_id).first()
    if _attempt(record) == attempt:
        return
    if attempt:
        records.update_or_create(turno_id=turn_id,
            defaults={"progreso": {"attempt": attempt}, "registrado_por_id": actor_id})
    else:
        records.filter(turno_id=turn_id).delete()


STALE_ATTEMPT = "El cierre cambió. Vuelve a Turno de caja para continuar con el cierre vigente."


def validate_final(turn, invoices, details, cash, *, attempt=""):
    record = _record(turn)
    if attempt != _attempt(record):
        raise ValueError(STALE_ATTEMPT)
    progress = _progress(record)
    if not progress:
        return  # Older tabs and signed offline events remain compatible.
    total, rows = validate(invoices, details)
    if str(total) != progress["invoices"] or rows != progress["payment_details"]:
        raise ValueError("Los pagos no coinciden con el paso ya confirmado. Recarga para recuperar el cierre guardado.")
    if progress["step"] == "media":
        expected = sum(DENOMINATIONS[key] * count for key, count in progress["denominations"].items())
        if Decimal(str(cash)) != expected:
            raise ValueError("El efectivo no coincide con el conteo ya confirmado.")


@login_required
@require_http_methods(["GET", "POST"])
def checkpoint(request, turno_id):
    from mainApp.models import AvanceCierreCaja, TurnoCaja
    from mainApp.views import _can_operate_turno
    with transaction.atomic():
        query = TurnoCaja.objects.select_for_update() if request.method == "POST" else TurnoCaja.objects
        turn = get_object_or_404(query, pk=turno_id)
        if not _can_operate_turno(request.user, turn):
            return JsonResponse({"success": False, "error": "No puedes operar el cierre de otro cajero."}, status=403)
        if turn.estado != "CIERRE":
            return JsonResponse({"success": False, "error": "El turno ya no está en cierre.", "closed": turn.estado == "CERRADO"}, status=409)
        record = _record(turn)
        attempt = _attempt(record)
        previous = _progress(record)
        if request.method == "POST":
            try:
                if len(request.body) > 60000:
                    raise ValueError("El avance supera el tamaño permitido.")
                data = json.loads(request.body)
                new = normalize(data)
            except (ValueError, TypeError) as exc:
                return JsonResponse({"success": False, "error": str(exc)}, status=400)
            if data.get("attempt", "") != attempt:
                return JsonResponse({"success": False, "error": STALE_ATTEMPT}, status=409)
            if previous and (not compatible(previous, new)
                    or (previous["step"] == "media" and new["step"] != "media")):
                return JsonResponse({"success": False, "conflict": True,
                    "progress": previous, "error": "Otra pestaña ya confirmó estos valores. Recarga para recuperar el avance guardado."}, status=409)
            # Imports a validated legacy/offline checkpoint without inventing payments.
            if previous != new:
                data = {**new, "attempt": attempt} if attempt else new
                AvanceCierreCaja.objects.update_or_create(turno=turn,
                    defaults={"progreso": data, "registrado_por": request.user})
            previous = new
        response = JsonResponse({"success": True, "progress": previous, "attempt": attempt})
        response["Cache-Control"] = "no-store, private"
        return response
