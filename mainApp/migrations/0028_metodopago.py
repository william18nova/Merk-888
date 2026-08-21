import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


DEFAULT_METHODS = (
    ("efectivo", "Efectivo", True, True, 10),
    ("nequi", "Nequi", False, True, 20),
    ("daviplata", "Daviplata", False, True, 30),
    ("tarjeta", "Tarjeta", False, True, 40),
    ("banco_caja_social", "Banco Caja Social", False, True, 50),
)


def seed_payment_methods(apps, schema_editor):
    MetodoPago = apps.get_model("mainApp", "MetodoPago")
    for code, name, is_cash, is_system, order in DEFAULT_METHODS:
        MetodoPago.objects.update_or_create(
            codigo=code,
            defaults={
                "nombre": name,
                "activo": True,
                "es_efectivo": is_cash,
                "es_sistema": is_system,
                "orden": order,
                "version": 1,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0027_configuracion_impresion_corte_automatico"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="MetodoPago",
            fields=[
                (
                    "codigo",
                    models.CharField(max_length=50, primary_key=True, serialize=False),
                ),
                ("nombre", models.CharField(max_length=80)),
                ("activo", models.BooleanField(db_index=True, default=True)),
                ("es_efectivo", models.BooleanField(default=False)),
                ("es_sistema", models.BooleanField(default=False)),
                ("orden", models.PositiveSmallIntegerField(default=100)),
                ("version", models.PositiveBigIntegerField(default=1)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("actualizado_en", models.DateTimeField(auto_now=True)),
                (
                    "actualizado_por_nombre",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "actualizado_por",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="metodos_pago_actualizados",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "metodos_pago",
                "ordering": ["orden", "nombre", "codigo"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("version__gte", 1)),
                        name="metodo_pago_version_positiva",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("es_efectivo", True)),
                        fields=("es_efectivo",),
                        name="metodo_pago_un_solo_efectivo",
                    ),
                ],
            },
        ),
        migrations.RunPython(seed_payment_methods, migrations.RunPython.noop),
    ]
