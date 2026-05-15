from django.db import migrations


def grant_cajero_print_sales(apps, schema_editor):
    Rol = apps.get_model("mainApp", "Rol")
    Permiso = apps.get_model("mainApp", "Permiso")

    cajero = Rol.objects.filter(nombre__iexact="Cajero").first()
    if not cajero:
        return

    permiso = (
        Permiso.objects.filter(nombre__iexact="Imprimir venta").first()
        or Permiso.objects.filter(nombre__iexact="ventas_imprimir").first()
    )
    if not permiso:
        permiso = Permiso.objects.create(
            nombre="Imprimir venta",
            descripcion="Permite abrir el detalle de una venta en modo solo lectura e imprimir la factura.",
        )

    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            DELETE FROM rolespermisos
            WHERE rolid = %s
              AND permisoid IN (
                SELECT permisoid
                FROM permisos
                WHERE lower(nombre) IN (
                  'visualizar ventas',
                  'ventas_ver',
                  'cambios y devoluciones',
                  'ventas_cambios'
                )
              )
            """,
            [cajero.pk],
        )
        cursor.execute(
            "SELECT 1 FROM rolespermisos WHERE rolid = %s AND permisoid = %s LIMIT 1",
            [cajero.pk, permiso.pk],
        )
        if cursor.fetchone():
            return
        cursor.execute(
            "INSERT INTO rolespermisos (rolid, permisoid) VALUES (%s, %s)",
            [cajero.pk, permiso.pk],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0013_sync_new_permissions"),
    ]

    operations = [
        migrations.RunPython(grant_cajero_print_sales, migrations.RunPython.noop),
    ]
