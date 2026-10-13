from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0049_solicitudpos")]
    operations = [migrations.CreateModel(
        name="DesglosePagosCierre",
        fields=[
            ("turno", models.OneToOneField(primary_key=True, serialize=False,
                related_name="desglose_pagos_cierre", on_delete=django.db.models.deletion.CASCADE, to="mainApp.turnocaja")),
            ("total", models.DecimalField(max_digits=12, decimal_places=2)),
            ("pagos", models.JSONField(default=list)),
            ("registrado_en", models.DateTimeField(auto_now=True)),
            ("registrado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.usuario")),
        ],
        options={"db_table": "desglose_pagos_cierre"},
    )]
