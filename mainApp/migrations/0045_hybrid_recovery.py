import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0044_hybrid_pilot")]
    operations = [migrations.CreateModel(
        name="RecuperacionHibrida",
        fields=[
            ("id", models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, serialize=False)),
            ("creada_en", models.DateTimeField(auto_now_add=True)),
            ("aprobada_en", models.DateTimeField(null=True, blank=True)),
            ("completada_en", models.DateTimeField(null=True, blank=True)),
            ("vence_en", models.DateTimeField()),
            ("estado", models.CharField(max_length=16, default="issued")),
            ("motivo", models.CharField(max_length=250)),
            ("codigo_hash", models.CharField(max_length=64)),
            ("nuevo_token_hash", models.CharField(max_length=64, blank=True)),
            ("manifiesto_hash", models.CharField(max_length=64, blank=True)),
            ("resumen", models.JSONField(default=dict)),
            ("resultado", models.JSONField(default=dict)),
            ("equipo", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.equipohibrido")),
            ("creada_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recuperaciones_hibridas_creadas", to=settings.AUTH_USER_MODEL)),
            ("aprobada_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recuperaciones_hibridas_aprobadas", null=True, blank=True, to=settings.AUTH_USER_MODEL)),
        ],
        options={"db_table": "recuperaciones_hibridas", "constraints": [models.UniqueConstraint(fields=("equipo",), condition=models.Q(estado__in=["issued", "review", "approved"]), name="hibrido_recuperacion_pendiente")]},
    )]
