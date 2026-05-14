from django.db import migrations


def sync_new_permissions(apps, schema_editor):
    from mainApp.permissions import grant_all_permissions_to_web_master, sync_permission_catalog

    sync_permission_catalog()
    grant_all_permissions_to_web_master(role_id=1)


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0012_notificacionnequi_venta_usado"),
    ]

    operations = [
        migrations.RunPython(sync_new_permissions, migrations.RunPython.noop),
    ]
