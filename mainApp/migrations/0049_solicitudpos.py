from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0048_hybrid_business_operations")]
    operations = [migrations.CreateModel(
        name="SolicitudPOS",
        fields=[
            ("id", models.UUIDField(primary_key=True, editable=False, serialize=False)),
            ("accion", models.CharField(max_length=30)),
            ("huella", models.CharField(max_length=64)),
            ("respuesta", models.JSONField(default=dict)),
            ("creada_en", models.DateTimeField(auto_now_add=True)),
            ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.usuario")),
            ("venta", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="mainApp.venta")),
        ],
        options={"db_table": "solicitudes_pos"},
    )]
