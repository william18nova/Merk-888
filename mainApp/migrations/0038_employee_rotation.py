import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0037_employee_schedule")]
    operations = [
        migrations.CreateModel(
            name="RotacionEmpleado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=120)),
                ("inicio", models.DateField()),
                ("patron", models.JSONField(default=list)),
                ("version", models.PositiveIntegerField(default=1)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("creado_por", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="mainApp.usuario")),
            ],
            options={"db_table": "rotaciones_empleados"},
        ),
        migrations.CreateModel(
            name="MiembroRotacionEmpleado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("empleado", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.empleado")),
                ("sucursal", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.sucursal")),
                ("rotacion", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="miembros", to="mainApp.rotacionempleado")),
            ],
            options={"db_table": "miembros_rotaciones_empleados", "constraints": [models.UniqueConstraint(fields=["rotacion", "empleado", "sucursal"], name="rotacion_miembro_sucursal_uniq")]},
        ),
        migrations.CreateModel(
            name="CambioRotacionEmpleado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("clave", models.PositiveIntegerField(default=0)),
                ("fecha_base", models.DateField()),
                ("alcance", models.CharField(max_length=12)),
                ("datos", models.JSONField(default=dict)),
                ("usuario_nombre", models.CharField(max_length=160)),
                ("origen", models.CharField(max_length=10)),
                ("solicitud_id", models.UUIDField(unique=True)),
                ("peticion", models.JSONField(default=dict)),
                ("anterior", models.JSONField(default=dict)),
                ("nuevo", models.JSONField(default=dict)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("usuario", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="mainApp.usuario")),
                ("rotacion", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cambios", to="mainApp.rotacionempleado")),
            ],
            options={"db_table": "cambios_rotaciones_empleados", "ordering": ["pk"], "indexes": [models.Index(fields=["rotacion", "clave", "fecha_base"], name="rotacion_cambio_fecha_idx")]},
        ),
    ]
