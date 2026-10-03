import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0043_expense_four_per_thousand")]

    operations = [
        migrations.CreateModel(
            name="EquipoHibrido",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("nombre", models.CharField(max_length=80)),
                ("activo", models.BooleanField(default=True)),
                ("token_hash", models.CharField(blank=True, max_length=64)),
                ("enlace_hash", models.CharField(max_length=64)),
                ("enlace_vence", models.DateTimeField()),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("visto_en", models.DateTimeField(blank=True, null=True)),
                ("creado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("punto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.puntospago")),
            ],
            options={"db_table": "equipos_hibridos", "constraints": [models.UniqueConstraint(condition=models.Q(activo=True), fields=("punto",), name="hibrido_equipo_activo_por_caja")]},
        ),
        migrations.CreateModel(
            name="SesionHibrida",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("creada_en", models.DateTimeField(auto_now_add=True)),
                ("vence_en", models.DateTimeField()),
                ("liberada_en", models.DateTimeField(blank=True, null=True)),
                ("secuencia", models.PositiveBigIntegerField(default=0)),
                ("equipo", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.equipohibrido")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("turno", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="mainApp.turnocaja")),
            ],
            options={"db_table": "sesiones_hibridas", "constraints": [models.UniqueConstraint(condition=models.Q(liberada_en__isnull=True), fields=("equipo",), name="hibrido_sesion_por_equipo")]},
        ),
        migrations.CreateModel(
            name="OperacionHibrida",
            fields=[
                ("id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("secuencia", models.PositiveBigIntegerField()),
                ("huella", models.CharField(max_length=64)),
                ("recibida_en", models.DateTimeField(auto_now_add=True)),
                ("ocurrida_en", models.DateTimeField()),
                ("respuesta", models.JSONField()),
                ("sesion", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.sesionhibrida")),
                ("venta", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to="mainApp.venta")),
            ],
            options={"db_table": "operaciones_hibridas", "constraints": [models.UniqueConstraint(fields=("sesion", "secuencia"), name="hibrido_secuencia_unica")]},
        ),
    ]
