import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0020_sync_employees_as_clients"),
    ]

    operations = [
        # TurnoCaja existe en la base y en models.py, pero nunca fue incluido
        # en el historial antiguo de migraciones. Este modelo no administrado
        # solo completa el estado para poder crear la FK sin tocar su tabla.
        migrations.CreateModel(
            name="TurnoCaja",
            fields=[
                (
                    "id",
                    models.AutoField(primary_key=True, serialize=False),
                ),
            ],
            options={
                "db_table": "turnos_caja",
                "managed": False,
            },
        ),
        migrations.CreateModel(
            name="ReintegroVenta",
            fields=[
                (
                    "reintegroid",
                    models.BigAutoField(
                        db_column="reintegroid",
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "medio_pago",
                    models.CharField(db_column="medio_pago", max_length=50),
                ),
                (
                    "monto",
                    models.DecimalField(decimal_places=2, max_digits=12),
                ),
                (
                    "creado_en",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "registrado_por",
                    models.ForeignKey(
                        blank=True,
                        db_column="registrado_por_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reintegros_registrados",
                        to="mainApp.usuario",
                    ),
                ),
                (
                    "turno",
                    models.ForeignKey(
                        db_column="turno_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="reintegros",
                        to="mainApp.turnocaja",
                    ),
                ),
                (
                    "venta",
                    models.ForeignKey(
                        db_column="ventaid",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reintegros",
                        to="mainApp.venta",
                    ),
                ),
            ],
            options={
                "db_table": "venta_reintegros",
                "ordering": ["-creado_en", "-reintegroid"],
            },
        ),
        migrations.AddConstraint(
            model_name="reintegroventa",
            constraint=models.CheckConstraint(
                condition=models.Q(("monto__gt", 0)),
                name="venta_reintegros_monto_positivo",
            ),
        ),
    ]
