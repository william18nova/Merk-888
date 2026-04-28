from django.db import migrations


def grant_permissions(apps, schema_editor):
    from mainApp.permissions import grant_all_permissions_to_web_master

    grant_all_permissions_to_web_master(role_id=1)


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0009_usuariopermiso"),
    ]

    operations = [
        migrations.RunPython(grant_permissions, migrations.RunPython.noop),
    ]
