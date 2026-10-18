"""Additive PTM cash-pool ledger. No existing cash balances are rewritten."""
import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0051_ptm_reader")]
    operations = [
        migrations.CreateModel(name="LecturaDiariaPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("fecha", models.DateField()),
            ("completada_en", models.DateTimeField()),
            ("cuenta", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.cuentalecturaptm")),
        ], options={"constraints": [models.UniqueConstraint(fields=["cuenta", "fecha"], name="ptm_lectura_diaria_unica")]}),
        migrations.CreateModel(name="BolsaEfectivoPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("base_inicial", models.DecimalField(decimal_places=2, max_digits=14)),
            ("inicio", models.DateTimeField()),
            ("version", models.PositiveIntegerField(default=1)),
            ("creada_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ("cuenta", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to="mainApp.cuentalecturaptm")),
            ("sucursal", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, to="mainApp.sucursal")),
        ]),
        migrations.CreateModel(name="AjusteBolsaPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("monto", models.DecimalField(decimal_places=2, max_digits=14)),
            ("motivo", models.CharField(max_length=300)),
            ("creado_en", models.DateTimeField()),
            ("solicitud", models.UUIDField(default=uuid.uuid4, unique=True)),
            ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ("bolsa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ajustes", to="mainApp.bolsaefectivoptm")),
        ]),
        migrations.AddField(model_name="operacionptm", name="bolsa", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="mainApp.bolsaefectivoptm")),
        migrations.CreateModel(name="CorteBolsaPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("accion", models.CharField(choices=[("entrada", "Recepción"), ("salida", "Entrega")], max_length=10)),
            ("solicitud", models.UUIDField(default=uuid.uuid4, unique=True)),
            ("desde", models.DateTimeField()),
            ("hasta", models.DateTimeField()),
            ("base", models.DecimalField(decimal_places=2, max_digits=14)),
            ("contado", models.DecimalField(decimal_places=2, max_digits=14)),
            ("esperado", models.DecimalField(decimal_places=2, max_digits=14)),
            ("diferencia", models.DecimalField(decimal_places=2, max_digits=14)),
            ("estado", models.CharField(default="provisional", max_length=16)),
            ("advertencias", models.JSONField(default=list)),
            ("firma_fuentes", models.CharField(max_length=64)),
            ("revision", models.PositiveIntegerField(default=1)),
            ("actor", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ("bolsa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cortes", to="mainApp.bolsaefectivoptm")),
            ("participantes", models.ManyToManyField(related_name="incidencias_bolsa_ptm", to="mainApp.turnocaja")),
            ("turno", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="cortes_realizados_ptm", to="mainApp.turnocaja")),
        ], options={"ordering": ["-hasta", "-pk"]}),
        migrations.CreateModel(name="AuditoriaBolsaPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("accion", models.CharField(max_length=24)),
            ("detalle", models.JSONField(default=dict)),
            ("creado_en", models.DateTimeField(auto_now_add=True)),
            ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ("bolsa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="auditoria", to="mainApp.bolsaefectivoptm")),
            ("corte", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="mainApp.cortebolsaptm")),
        ]),
        migrations.CreateModel(name="ParticipacionBolsaPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("ingreso", models.DateTimeField()),
            ("salida", models.DateTimeField(blank=True, null=True)),
            ("bolsa", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="participaciones", to="mainApp.bolsaefectivoptm")),
            ("turno", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="participacion_bolsa_ptm", to="mainApp.turnocaja")),
        ]),
        migrations.AddConstraint(model_name="cortebolsaptm", constraint=models.UniqueConstraint(fields=("turno", "accion"), name="ptm_corte_turno_accion_unico")),
    ]
