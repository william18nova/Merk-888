from decimal import Decimal

from django.db import migrations, models


def enable_nequi_and_card(apps, schema_editor):
    methods = apps.get_model("mainApp", "MetodoPago")
    methods.objects.using(schema_editor.connection.alias).filter(
        codigo__in=["nequi", "tarjeta"],
    ).update(aplica_4xmil_egresos=True)


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0042_telegram_assistant_workspace")]

    operations = [
        migrations.AddField(
            model_name="metodopago", name="aplica_4xmil_egresos",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="egreso", name="aplica_4xmil",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="egreso", name="impuesto_4xmil",
            field=models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00")),
        ),
        migrations.AddConstraint(
            model_name="egreso",
            constraint=models.CheckConstraint(
                condition=models.Q(impuesto_4xmil__gte=0) & models.Q(impuesto_4xmil__lt=models.F("monto")),
                name="egreso_impuesto_4xmil_valido",
            ),
        ),
        migrations.AddConstraint(
            model_name="egreso",
            constraint=models.CheckConstraint(
                condition=models.Q(aplica_4xmil=True) | models.Q(impuesto_4xmil=0),
                name="egreso_sin_4xmil_impuesto_cero",
            ),
        ),
        # No se recalculan pagos históricos. Primero DDL, después datos, para
        # evitar eventos de triggers pendientes en PostgreSQL.
        migrations.RunPython(enable_nequi_and_card, migrations.RunPython.noop),
    ]
