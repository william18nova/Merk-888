from django.db import migrations


NEQUI_API_FEATURE = "nequi_api_recepcion"


def seed_nequi_api_feature(apps, schema_editor):
    ConfiguracionFuncionalidad = apps.get_model(
        "mainApp",
        "ConfiguracionFuncionalidad",
    )
    ConfiguracionFuncionalidad.objects.get_or_create(
        clave=NEQUI_API_FEATURE,
        defaults={
            "habilitada": True,
            "version": 1,
            "actualizada_por_nombre": "Configuración inicial",
        },
    )


def keep_feature_history(apps, schema_editor):
    # Revertir código no debe borrar una decisión ni su historial de auditoría.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0024_system_feature_configuration"),
    ]

    operations = [
        migrations.RunPython(
            seed_nequi_api_feature,
            keep_feature_history,
        ),
    ]
