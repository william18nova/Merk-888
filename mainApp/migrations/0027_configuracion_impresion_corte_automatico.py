from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0026_configuracion_impresion"),
    ]

    operations = [
        migrations.AddField(
            model_name="configuracionimpresion",
            name="corte_automatico",
            field=models.BooleanField(default=True),
        ),
    ]
