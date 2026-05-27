from django.db import migrations


PERMISSION_NAME = "Metricas del negocio"
PERMISSION_DESCRIPTION = "Permite consultar metricas estadisticas generales del negocio."


def _normalize(value):
    return " ".join(str(value or "").strip().lower().split())


def sync_metricas_permission(apps, schema_editor):
    Permiso = apps.get_model("mainApp", "Permiso")
    Rol = apps.get_model("mainApp", "Rol")
    RolPermiso = apps.get_model("mainApp", "RolPermiso")

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
        RolPermiso.objects.get_or_create(rol=web_master, permiso=permission)


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0017_sync_ventas_no_realizadas_permission"),
    ]

    operations = [
        migrations.RunPython(sync_metricas_permission, migrations.RunPython.noop),
    ]
