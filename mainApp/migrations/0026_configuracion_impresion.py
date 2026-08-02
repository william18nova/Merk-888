import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0025_seed_nequi_api_feature"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConfiguracionImpresion",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "sistema_operativo",
                    models.CharField(
                        choices=[("windows", "Windows"), ("linux", "Linux")],
                        default="windows",
                        max_length=10,
                    ),
                ),
                (
                    "tamano_factura",
                    models.CharField(
                        choices=[
                            ("grande", "Grande (80 mm)"),
                            ("pequena", "Pequeña (58 mm)"),
                        ],
                        default="grande",
                        max_length=10,
                    ),
                ),
                ("version", models.PositiveBigIntegerField(default=1)),
                ("actualizada_en", models.DateTimeField(auto_now=True)),
                (
                    "actualizada_por_nombre",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "actualizada_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="configuraciones_impresion_actualizadas",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "punto_pago",
                    models.OneToOneField(
                        db_column="puntopagoid",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="configuracion_impresion",
                        to="mainApp.puntospago",
                    ),
                ),
            ],
            options={
                "db_table": "configuracion_impresion",
                "ordering": ["punto_pago_id"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("sistema_operativo__in", ["windows", "linux"]),
                        ),
                        name="config_impresion_so_valido",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("tamano_factura__in", ["grande", "pequena"]),
                        ),
                        name="config_impresion_tamano_valido",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="config_impresion_version_positiva",
                    ),
                ],
            },
        ),
    ]
