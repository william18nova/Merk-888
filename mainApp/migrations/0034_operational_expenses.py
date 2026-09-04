from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0033_product_category_integrity"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ConceptoEgreso",
            fields=[
                (
                    "conceptoid",
                    models.BigAutoField(
                        db_column="conceptoid",
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("nombre", models.CharField(max_length=160, unique=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                (
                    "creado_por",
                    models.ForeignKey(
                        blank=True,
                        db_column="creado_por_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="conceptos_egreso_creados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "conceptos_egreso",
                "ordering": ["nombre"],
                "constraints": [
                    models.CheckConstraint(
                        condition=~models.Q(nombre=""),
                        name="concepto_egreso_nombre_no_vacio",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="Egreso",
            fields=[
                (
                    "egresoid",
                    models.BigAutoField(
                        db_column="egresoid",
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("monto", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "medio_pago",
                    models.CharField(
                        db_column="medio_pago",
                        db_index=True,
                        max_length=50,
                    ),
                ),
                ("registrado_por_nombre", models.CharField(max_length=160)),
                ("creado_en", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "concepto",
                    models.ForeignKey(
                        db_column="conceptoid",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="egresos",
                        to="mainApp.conceptoegreso",
                    ),
                ),
                (
                    "registrado_por",
                    models.ForeignKey(
                        blank=True,
                        db_column="registrado_por_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="egresos_registrados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "egresos",
                "ordering": ["-creado_en", "-egresoid"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(monto__gt=0),
                        name="egreso_monto_positivo",
                    ),
                ],
            },
        ),
    ]
