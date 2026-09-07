import django.db.models.deletion
from django.db import migrations, models


def seed_permissions(apps, schema_editor):
    permission = apps.get_model("mainApp", "Permiso")
    for name, description in (
        ("Ver calendario de empleados", "Permite consultar la planificación laboral de todos los empleados."),
        ("Gestionar turnos de empleados", "Permite crear, mover, editar y cancelar jornadas, sin modificar cajas."),
    ):
        if not permission.objects.using(schema_editor.connection.alias).filter(nombre__iexact=name).exists():
            permission.objects.using(schema_editor.connection.alias).create(nombre=name, descripcion=description)


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0036_telegram_intelligent_bot")]
    operations = [
        migrations.CreateModel(
            name="TurnoEmpleado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("inicio", models.DateTimeField(db_index=True)),
                ("fin", models.DateTimeField()),
                ("notas", models.CharField(blank=True, default="", max_length=300)),
                ("cancelado", models.BooleanField(default=False)),
                ("version", models.PositiveIntegerField(default=1)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                ("empleado", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="turnos_planificados", to="mainApp.empleado")),
                ("sucursal", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="turnos_empleados", to="mainApp.sucursal")),
                ("creado_por", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="horarios_creados", to="mainApp.usuario")),
                ("actualizado_por", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="horarios_actualizados", to="mainApp.usuario")),
            ],
            options={
                "db_table": "turnos_empleados", "ordering": ["inicio", "pk"],
                "indexes": [models.Index(fields=["empleado", "inicio", "fin"], name="turno_emp_intervalo_idx")],
                "constraints": [
                    models.CheckConstraint(condition=models.Q(fin__gt=models.F("inicio")), name="turno_emp_fin_posterior"),
                    models.CheckConstraint(condition=models.Q(version__gte=1), name="turno_emp_version_positiva"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CambioTurnoEmpleado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("usuario_nombre", models.CharField(max_length=160)),
                ("operacion", models.CharField(max_length=10)),
                ("origen", models.CharField(max_length=10)),
                ("solicitud_id", models.UUIDField(unique=True)),
                ("peticion", models.JSONField(default=dict)),
                ("anterior", models.JSONField(default=dict)),
                ("nuevo", models.JSONField(default=dict)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("turno", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cambios", to="mainApp.turnoempleado")),
                ("usuario", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to="mainApp.usuario")),
            ],
            options={"db_table": "cambios_turnos_empleados", "ordering": ["-creado_en", "-pk"]},
        ),
        migrations.RunPython(seed_permissions, migrations.RunPython.noop),
    ]
