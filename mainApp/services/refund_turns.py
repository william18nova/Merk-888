"""Assign a web refund to a live shift without changing the sale's original register."""
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Exists, OuterRef, Q

from mainApp.hybrid_models import SesionHibrida
from mainApp.models import AvanceCierreCaja, TurnoCaja

from . import close_progress, hybrid


ACTIVE = ("ABIERTO", "CIERRE")
NO_TURN = "sin_turno"


def _branch_turns(sale):
    return TurnoCaja.objects.filter(
        puntopago__sucursalid_id=sale.sucursalid_id, estado__in=ACTIVE,
    ).order_by("-inicio", "-pk")


def choices(sale):
    active = _branch_turns(sale)
    original_id = active.filter(puntopago_id=sale.puntopagoid_id).values_list("pk", flat=True).first()
    counted = AvanceCierreCaja.objects.filter(turno_id=OuterRef("pk"), progreso__step="media")
    eligible = active.exclude(Q(estado="CIERRE") & Exists(counted))
    if SesionHibrida._meta.db_table in connection.introspection.table_names():
        reserved = SesionHibrida.objects.filter(turno_id=OuterRef("pk"), liberada_en__isnull=True)
        eligible = eligible.filter(~Exists(reserved))
    turns = list(eligible.select_related("puntopago", "cajero"))
    return {
        "turns": turns,
        "default_id": original_id if any(turn.pk == original_id for turn in turns) else None,
        "allow_no_turn": original_id is None,
        "no_turn_value": NO_TURN,
    }


def lock_destination(sale, choice, *, turn_required):
    """Revalidate while locked; a stale selection never falls back to another box."""
    if not turn_required:
        if choice is not None:
            raise ValueError("El control de turnos cambió. Recarga la venta antes de registrar la devolución.")
        return None

    active = _branch_turns(sale).select_for_update(of=("self",))
    if choice is None or choice == NO_TURN:
        # Older forms default to the original register. A requested no-turn
        # refund is only valid while that register really has no active shift.
        turn = active.filter(puntopago_id=sale.puntopagoid_id).first()
        if turn and choice == NO_TURN:
            raise ValueError("La caja original ya tiene un turno activo. Recarga y selecciona un turno para el reintegro.")
    else:
        if len(choice) > 19 or not choice.isascii() or not choice.isdecimal() or not 0 < int(choice) < 2**63:
            raise ValueError("Selecciona un turno válido para la devolución.")
        turn = active.filter(pk=int(choice)).first()
        if not turn:
            raise ValueError("El turno elegido no está activo en la sucursal de esta venta. No se aplicó la devolución.")

    if turn:
        try:
            hybrid.assert_turn_released(turn.pk)
        except ValidationError:
            raise ValueError("Ese turno tiene una sesión híbrida activa. Sincronízala y finalízala antes de cargarle una devolución.") from None
        if turn.estado == "CIERRE" and (close_progress.confirmed_progress(turn) or {}).get("step") == "media":
            raise ValueError("Ese turno ya confirmó el efectivo. Reábrelo antes de registrar la devolución allí, o elige otro turno donde salió el dinero.")
    return turn
