import re

from django.db import migrations


def _normalize_document(value):
    return re.sub(r"[^0-9A-Za-z]+", "", str(value or "")).lower()


def sync_existing_employees_as_clients(apps, schema_editor):
    Empleado = apps.get_model("mainApp", "Empleado")
    Cliente = apps.get_model("mainApp", "Cliente")
    alias = schema_editor.connection.alias

    clients_by_document = {}
    for client in Cliente.objects.using(alias).order_by("clienteid").iterator():
        document = _normalize_document(client.numerodocumento)
        if not document:
            continue
        if document in clients_by_document:
            raise RuntimeError(
                f"Hay clientes duplicados para el documento normalizado {document}."
            )
        clients_by_document[document] = client

    employees = []
    employees_by_document = {}
    for employee in Empleado.objects.using(alias).order_by("empleadoid").iterator():
        document = _normalize_document(employee.numerodocumento)
        if not document:
            raise RuntimeError(
                f"El empleado {employee.pk} no tiene un documento válido."
            )
        if document in employees_by_document:
            raise RuntimeError(
                "Los empleados "
                f"{employees_by_document[document]} y {employee.pk} "
                f"comparten el documento normalizado {document}."
            )
        employees_by_document[document] = employee.pk
        employees.append((employee, document))

    for employee, document in employees:
        values = {
            "numerodocumento": document,
            "nombre": str(employee.nombre or "").strip(),
            "apellido": str(employee.apellido or "").strip(),
            "telefono": str(employee.telefono or "").strip(),
            "email": str(employee.email or "").strip(),
        }
        client = clients_by_document.get(document)
        if client is None:
            client = Cliente.objects.using(alias).create(**values)
            clients_by_document[document] = client
            continue

        changed_fields = []
        for field, value in values.items():
            if field != "numerodocumento" and not value:
                continue
            if getattr(client, field) != value:
                setattr(client, field, value)
                changed_fields.append(field)
        if changed_fields:
            client.save(using=alias, update_fields=changed_fields)


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0019_inventario_unique_sucursal_producto"),
    ]

    operations = [
        migrations.RunPython(
            sync_existing_employees_as_clients,
            migrations.RunPython.noop,
        ),
    ]
