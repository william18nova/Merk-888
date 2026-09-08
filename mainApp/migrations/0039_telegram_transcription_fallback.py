from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0038_employee_rotation")]

    operations = [
        migrations.AddField(model_name="telegramactualizacion", name="transcripcion_estado",
                            field=models.JSONField(blank=True, default=dict)),
        migrations.AddField(model_name="telegramactualizacion", name="reintentar_en",
                            field=models.DateTimeField(blank=True, null=True)),
    ]
