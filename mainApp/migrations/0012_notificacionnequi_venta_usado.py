import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0011_notificacionnequi"),
    ]

    operations = [
        migrations.AddField(
            model_name="notificacionnequi",
            name="venta",
            field=models.ForeignKey(
                blank=True,
                db_column="ventaid",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="notificaciones_nequi",
                to="mainApp.venta",
            ),
        ),
        migrations.AddField(
            model_name="notificacionnequi",
            name="usado_en",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="notificacionnequi",
            index=models.Index(fields=["venta"], name="notificacio_ventaid_41c4f5_idx"),
        ),
    ]
