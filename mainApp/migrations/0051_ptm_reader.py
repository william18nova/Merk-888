import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0050_desglosepagoscierre")]
    operations = [
        migrations.CreateModel(name="CuentaLecturaPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("nombre", models.CharField(max_length=80)),
            ("usuario_ptm", models.CharField(max_length=100, unique=True)),
            ("creada_en", models.DateTimeField(auto_now_add=True)),
        ]),
        migrations.CreateModel(name="LectorPTM", fields=[
            ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
            ("nombre", models.CharField(max_length=80)),
            ("token_hash", models.CharField(max_length=64)),
            ("creado_en", models.DateTimeField(auto_now_add=True)),
            ("vence_en", models.DateTimeField()),
            ("activo", models.BooleanField(default=True)),
            ("ultimo_contacto", models.DateTimeField(blank=True, null=True)),
            ("ultima_lectura", models.DateTimeField(blank=True, null=True)),
            ("fecha_reporte", models.DateField(blank=True, null=True)),
            ("estado", models.CharField(default="pendiente", max_length=24)),
            ("cuenta", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="lectores", to="mainApp.cuentalecturaptm")),
            ("creado_por", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.usuario")),
        ]),
        migrations.CreateModel(name="MovimientoLeidoPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("id_tx", models.CharField(max_length=100)),
            ("id_operador", models.CharField(blank=True, max_length=100)),
            ("inicio", models.DateTimeField()),
            ("fin", models.DateTimeField(blank=True, null=True)),
            ("producto", models.CharField(max_length=150)),
            ("valor", models.DecimalField(decimal_places=2, max_digits=14)),
            ("estado_ptm", models.CharField(max_length=80)),
            ("tipo", models.CharField(blank=True, max_length=10)),
            ("conciliacion", models.CharField(db_index=True, default="sin_vincular", max_length=16)),
            ("primera_lectura", models.DateTimeField(auto_now_add=True)),
            ("ultima_lectura", models.DateTimeField()),
            ("observado_en", models.DateTimeField()),
            ("cuenta", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.cuentalecturaptm")),
            ("operacion", models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="lectura_ptm", to="mainApp.operacionptm")),
        ], options={"ordering": ["-inicio", "-pk"], "constraints": [models.UniqueConstraint(
            fields=("cuenta", "id_tx"), name="ptm_lectura_cuenta_tx_unica")]}),
        migrations.CreateModel(name="AuditoriaLecturaPTM", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("accion", models.CharField(max_length=20)),
            ("detalle", models.JSONField(default=dict)),
            ("creado_en", models.DateTimeField(auto_now_add=True)),
            ("lector", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to="mainApp.lectorptm")),
            ("movimiento", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="auditoria", to="mainApp.movimientoleidoptm")),
            ("usuario", models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to="mainApp.usuario")),
        ]),
    ]
