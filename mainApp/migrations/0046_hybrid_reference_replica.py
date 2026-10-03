from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0045_hybrid_recovery")]
    operations = [migrations.CreateModel(
        name="ReplicaHibrida",
        fields=[
            ("id", models.UUIDField(primary_key=True, editable=False, serialize=False)),
            ("base_id", models.UUIDField(null=True)),
            ("solicitud_hash", models.CharField(max_length=64)),
            ("alcance", models.JSONField()), ("filas", models.JSONField()),
            ("cambios", models.JSONField()), ("resumen", models.JSONField()),
            ("huella", models.CharField(max_length=64)),
            ("creada_en", models.DateTimeField(auto_now_add=True)),
            ("vence_en", models.DateTimeField(db_index=True)),
            ("equipo", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="mainApp.equipohibrido")),
            ("sesion", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="mainApp.sesionhibrida")),
        ], options={"db_table": "replicas_hibridas"},
    )]
