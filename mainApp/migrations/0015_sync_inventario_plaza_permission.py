from django.db import migrations


def sync_inventario_plaza_permission(apps, schema_editor):
    from mainApp.permissions import grant_all_permissions_to_web_master, sync_permission_catalog

    sync_permission_catalog()
    grant_all_permissions_to_web_master(role_id=1)


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0014_grant_cajero_print_sales"),
    ]

    operations = [
        migrations.RunPython(sync_inventario_plaza_permission, migrations.RunPython.noop),
    ]
