from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0046_hybrid_reference_replica")]
    operations = [migrations.AddField(
        model_name="replicahibrida", name="cursor_ventas", field=models.JSONField(default=dict),
    )]
