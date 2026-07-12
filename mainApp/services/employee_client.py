import re

from mainApp.models import Cliente


class EmployeeClientSyncError(ValueError):
    """La identidad del cliente es ambigua y no puede sincronizarse con seguridad."""


def normalize_document(value):
    return re.sub(r"[^0-9A-Za-z]+", "", str(value or "")).lower()


def _matching_clients(document):
    canonical = normalize_document(document)
    if not canonical:
        return []

    matches = list(
        Cliente.objects
        .filter(numerodocumento__iexact=canonical)
        .order_by("clienteid")[:2]
    )
    if len(matches) == 2:
        return matches

    matched_ids = {client.pk for client in matches}
    for client in (
        Cliente.objects
        .only(
            "clienteid",
            "numerodocumento",
            "nombre",
            "apellido",
            "telefono",
            "email",
        )
        .exclude(pk__in=matched_ids)
        .order_by("clienteid")
        .iterator()
    ):
        if normalize_document(client.numerodocumento) == canonical:
            matches.append(client)
            if len(matches) == 2:
                break
    return matches


def _one_client_or_error(matches, document):
    if len(matches) > 1:
        raise EmployeeClientSyncError(
            f"Hay varios clientes con el documento {document}; "
            "corrige los duplicados antes de guardar el empleado."
        )
    return matches[0] if matches else None


def _employee_values(employee, canonical_document):
    return {
        "numerodocumento": canonical_document,
        "nombre": str(getattr(employee, "nombre", "") or "").strip(),
        "apellido": str(getattr(employee, "apellido", "") or "").strip(),
        "telefono": str(getattr(employee, "telefono", "") or "").strip(),
        "email": str(getattr(employee, "email", "") or "").strip(),
    }


def sync_employee_client(employee, previous_document=None):
    """Crea o sincroniza un Cliente usando exclusivamente la identidad documental."""
    current_document = normalize_document(
        getattr(employee, "numerodocumento", "")
    )
    if not current_document:
        raise EmployeeClientSyncError(
            "El empleado necesita un número de documento para crear su cliente."
        )

    current_client = _one_client_or_error(
        _matching_clients(current_document),
        current_document,
    )

    previous_client = None
    previous_document = normalize_document(previous_document)
    if previous_document and previous_document != current_document:
        previous_client = _one_client_or_error(
            _matching_clients(previous_document),
            previous_document,
        )

    if (
        current_client is not None
        and previous_client is not None
        and current_client.pk != previous_client.pk
    ):
        raise EmployeeClientSyncError(
            "El nuevo documento ya pertenece a otro cliente; "
            "no se mezclaron las dos personas."
        )

    values = _employee_values(employee, current_document)
    client = current_client or previous_client

    if client is None:
        return Cliente.objects.create(**values), True

    changed_fields = []
    for field, value in values.items():
        if field != "numerodocumento" and not value:
            continue
        if getattr(client, field) != value:
            setattr(client, field, value)
            changed_fields.append(field)

    if changed_fields:
        client.save(update_fields=changed_fields)
    return client, False
