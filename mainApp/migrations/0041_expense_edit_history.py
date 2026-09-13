import django.db.models.deletion
from django.db import migrations, models


def seed_permission(apps, schema_editor):
    permissions = apps.get_model("mainApp", "Permiso").objects.using(schema_editor.connection.alias)
    if not permissions.filter(nombre__iexact="Editar pagos registrados").exists():
        permissions.create(
            nombre="Editar pagos registrados",
            descripcion="Permite buscar y corregir pagos del negocio y consultar su historial de cambios.",
        )


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0040_ptm_cash_operations")]
    operations = [
        migrations.CreateModel(
            name="CambioEgreso",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("usuario_nombre", models.CharField(max_length=160)),
                ("motivo", models.CharField(max_length=300)),
                ("anterior", models.JSONField(default=dict)),
                ("nuevo", models.JSONField(default=dict)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("egreso", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cambios", to="mainApp.egreso")),
                ("usuario", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="pagos_corregidos", to="mainApp.usuario")),
            ],
            options={"db_table": "cambios_egresos", "ordering": ["-creado_en", "-pk"]},
        ),
        migrations.RunPython(seed_permission, migrations.RunPython.noop),
    ]
