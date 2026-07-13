from django.db import migrations


PERMISSION_NAME = "Metricas del negocio"
PERMISSION_DESCRIPTION = "Permite consultar metricas estadisticas generales del negocio."


def _normalize(value):
    return " ".join(str(value or "").strip().lower().split())


def sync_metricas_permission(apps, schema_editor):
    Permiso = apps.get_model("mainApp", "Permiso")
    Rol = apps.get_model("mainApp", "Rol")

    permission = None
    permission_names = {
        "metricas del negocio",
        "metricas negocio",
        "metricas_negocio",
        "analitica",
    }
    for candidate in Permiso.objects.all():
        if _normalize(candidate.nombre) in permission_names:
            permission = candidate
            break

    if permission is None:
        permission = Permiso.objects.create(
            nombre=PERMISSION_NAME,
            descripcion=PERMISSION_DESCRIPTION,
        )

    if not permission.descripcion:
        permission.descripcion = PERMISSION_DESCRIPTION
        permission.save(update_fields=["descripcion"])

    web_master = None
    for role in Rol.objects.all():
        if _normalize(role.nombre) in {"web master", "webmaster"}:
            web_master = role
            break

    if web_master:
        # El estado histórico de RolPermiso solo expone su PK porque la tabla
        # es managed=False. Usamos SQL parametrizado contra la tabla existente
        # para que esta migración siga siendo reproducible.
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM rolespermisos
                WHERE rolid = %s AND permisoid = %s
                """,
                [web_master.pk, permission.pk],
            )
            if cursor.fetchone() is None:
                cursor.execute(
                    """
                    INSERT INTO rolespermisos (rolid, permisoid)
                    VALUES (%s, %s)
                    """,
                    [web_master.pk, permission.pk],
                )


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0017_sync_ventas_no_realizadas_permission"),
    ]

    operations = [
        migrations.RunPython(sync_metricas_permission, migrations.RunPython.noop),
    ]
