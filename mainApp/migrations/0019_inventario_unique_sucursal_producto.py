from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0018_sync_metricas_permission"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="inventario",
            constraint=models.UniqueConstraint(
                fields=("sucursalid", "productoid"),
                name="uniq_inventario_sucursal_producto",
            ),
        ),
    ]
