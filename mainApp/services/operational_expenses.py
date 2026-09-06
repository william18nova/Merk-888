from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
import unicodedata

from django.db import transaction

from mainApp.models import (
    ConceptoEgreso,
    Egreso,
    MetodoPago,
    normalizar_nombre_concepto_egreso,
)
from mainApp.services.payment_methods import (
    DEFAULT_PAYMENT_METHODS,
    normalize_payment_method_code,
    payment_method_table_ready,
)


class OperationalExpenseError(ValueError):
    pass


def _concept_search_key(name):
    text = unicodedata.normalize("NFKD", normalizar_nombre_concepto_egreso(name))
    return "".join(char for char in text if char.isalnum())


def find_similar_expense_concepts(name, *, limit=5):
    """Sugiere conceptos reales sin modificar el catálogo ni elegir por el usuario."""
    normalized = normalizar_nombre_concepto_egreso(name)
    key = _concept_search_key(normalized)
    if not key or limit <= 0:
        return []
    matches = []
    for concept in ConceptoEgreso.objects.only("conceptoid", "nombre").iterator():
        candidate = _concept_search_key(concept.nombre)
        if not candidate:
            continue
        score = SequenceMatcher(None, key, candidate, autojunk=False).ratio()
        # Los nombres cortos requieren igualdad para evitar sugerencias accidentales.
        if key != candidate and (min(len(key), len(candidate)) < 4 or score < 0.78):
            continue
        matches.append((
            concept.nombre != normalized, -score, concept.nombre, concept.pk,
        ))
        matches.sort()
        del matches[limit:]
    return [{"id": pk, "nombre": label} for _, _, label, pk in matches]


def register_operational_expense(*, user, concept, amount, payment_method, concept_id=None):
    """Única ruta de dominio para registrar pagos desde web o Telegram."""

    concept_name = normalizar_nombre_concepto_egreso(concept)
    if not concept_name or len(concept_name) > 160:
        raise OperationalExpenseError(
            "El concepto debe tener entre 1 y 160 caracteres."
        )
    try:
        normalized_amount = Decimal(str(amount)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        raise OperationalExpenseError("El valor pagado no es válido.")
    if not normalized_amount.is_finite() or normalized_amount <= 0:
        raise OperationalExpenseError("El valor pagado debe ser mayor que cero.")
    if normalized_amount >= Decimal("1000000000000"):
        raise OperationalExpenseError("El valor pagado es demasiado grande.")

    method = normalize_payment_method_code(payment_method)
    if not method:
        raise OperationalExpenseError("Selecciona un medio de pago.")

    with transaction.atomic():
        if payment_method_table_ready():
            method_is_active = (
                MetodoPago.objects
                .select_for_update()
                .filter(pk=method, activo=True)
                .exists()
            )
        else:
            method_is_active = method in {
                row["code"]
                for row in DEFAULT_PAYMENT_METHODS
                if row["active"]
            }
        if not method_is_active:
            raise OperationalExpenseError(
                "Ese medio de pago está desactivado. Selecciona otro."
            )

        if concept_id is not None:
            expense_concept = (
                ConceptoEgreso.objects.select_for_update()
                .filter(pk=concept_id, nombre=concept_name)
                .first()
            )
            if expense_concept is None:
                raise OperationalExpenseError(
                    "El concepto elegido cambió o ya no existe. Solicita el pago nuevamente."
                )
        else:
            expense_concept, _created = ConceptoEgreso.objects.get_or_create(
                nombre=concept_name,
                defaults={"creado_por": user},
            )
        expense = Egreso.objects.create(
            concepto=expense_concept,
            monto=normalized_amount,
            medio_pago=method,
            registrado_por=user,
            registrado_por_nombre=(
                getattr(user, "nombreusuario", "")
                or str(user)
            )[:160],
        )
    return expense
