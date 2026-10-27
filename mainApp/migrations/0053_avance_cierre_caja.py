from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0052_ptm_cash_pool")]
    operations = [migrations.CreateModel(
        name="AvanceCierreCaja",
        fields=[
            ("turno", models.OneToOneField(primary_key=True, serialize=False, on_delete=django.db.models.deletion.CASCADE, to="mainApp.turnocaja")),
            ("progreso", models.JSONField(default=dict)),
            ("actualizado_en", models.DateTimeField(auto_now=True)),
            ("registrado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
        ],
        options={"db_table": "avance_cierre_caja"},
    )]
