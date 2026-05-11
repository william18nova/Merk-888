import django.utils.timezone
from django.db import migrations, models


def sync_nequi_permission(apps, schema_editor):
    from mainApp.permissions import grant_all_permissions_to_web_master, sync_permission_catalog

    sync_permission_catalog()
    grant_all_permissions_to_web_master(role_id=1)


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0010_grant_web_master_permissions"),
    ]

    operations = [
        migrations.CreateModel(
            name="NotificacionNequi",
            fields=[
                ("notificacionid", models.BigAutoField(primary_key=True, serialize=False)),
                ("titulo", models.CharField(blank=True, max_length=180)),
                ("texto", models.TextField()),
                ("app", models.CharField(blank=True, max_length=120)),
                ("paquete", models.CharField(blank=True, max_length=160)),
                ("monto", models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True)),
                ("remitente", models.CharField(blank=True, max_length=160)),
                ("referencia", models.CharField(blank=True, max_length=120)),
                ("recibido_en", models.DateTimeField(default=django.utils.timezone.now)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("raw_payload", models.JSONField(blank=True, default=dict)),
                ("fingerprint", models.CharField(db_index=True, max_length=64, unique=True)),
            ],
            options={
                "db_table": "notificaciones_nequi",
                "ordering": ["-recibido_en", "-notificacionid"],
                "indexes": [
                    models.Index(fields=["recibido_en"], name="notificacio_recibid_6f12d3_idx"),
                    models.Index(fields=["monto"], name="notificacio_monto_429441_idx"),
                ],
            },
        ),
        migrations.RunPython(sync_nequi_permission, migrations.RunPython.noop),
    ]
