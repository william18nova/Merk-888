from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0047_hybrid_replica_sale_cursor")]
    operations = [
        migrations.AlterField(model_name="operacionhibrida", name="venta",
            field=models.OneToOneField(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="mainApp.venta")),
        migrations.AddField(model_name="operacionhibrida", name="tipo",
            field=models.CharField(default="sale", max_length=30)),
    ]
