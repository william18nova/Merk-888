from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0030_rename_batavia_completa"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificacionnequi",
            name="es_ingreso",
            field=models.BooleanField(
                db_index=True,
                db_default=False,
                default=False,
                help_text=(
                    "Indica que la notificación corresponde a dinero recibido."
                ),
            ),
        ),
    ]
