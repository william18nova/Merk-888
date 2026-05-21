import django.utils.timezone
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0015_sync_inventario_plaza_permission"),
    ]

    operations = [
        migrations.CreateModel(
            name="VentaCarritoAudit",
            fields=[
                ("auditoriaid", models.BigAutoField(db_column="auditoriaid", primary_key=True, serialize=False)),
                ("evento", models.CharField(default="carrito_limpiado", max_length=40)),
                ("motivo", models.CharField(blank=True, default="", max_length=80)),
                ("usuarioid", models.PositiveIntegerField(blank=True, db_column="usuarioid", null=True)),
                ("usuario_nombre", models.CharField(blank=True, default="", max_length=160)),
                ("sucursalid", models.PositiveIntegerField(blank=True, db_column="sucursalid", null=True)),
                ("sucursal_nombre", models.CharField(blank=True, default="", max_length=120)),
                ("puntopagoid", models.PositiveIntegerField(blank=True, db_column="puntopagoid", null=True)),
                ("puntopago_nombre", models.CharField(blank=True, default="", max_length=120)),
                ("turnoid", models.PositiveIntegerField(blank=True, db_column="turnoid", null=True)),
                ("clienteid", models.PositiveIntegerField(blank=True, db_column="clienteid", null=True)),
                ("cliente_nombre", models.CharField(blank=True, default="", max_length=180)),
                ("subtotal", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=15)),
                ("descuento", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=15)),
                ("total", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=15)),
                ("cantidad_productos", models.PositiveIntegerField(default=0)),
                ("cantidad_unidades", models.DecimalField(decimal_places=3, default=Decimal("0.000"), max_digits=14)),
                ("productos", models.JSONField(blank=True, default=list)),
                ("user_agent", models.TextField(blank=True, default="")),
                ("ip", models.GenericIPAddressField(blank=True, null=True)),
                ("creado_en", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                "db_table": "ventas_carrito_auditoria",
                "ordering": ["-creado_en", "-auditoriaid"],
                "indexes": [
                    models.Index(fields=["creado_en"], name="vca_creado_idx"),
                    models.Index(fields=["usuarioid", "creado_en"], name="vca_usuario_fecha_idx"),
                    models.Index(fields=["sucursalid", "creado_en"], name="vca_sucursal_fecha_idx"),
                    models.Index(fields=["motivo", "creado_en"], name="vca_motivo_fecha_idx"),
                ],
            },
        ),
    ]
