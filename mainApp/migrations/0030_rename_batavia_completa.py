from django.db import migrations


PRODUCT_ID = 25063529
TARGET_NAME = "FR BATAVIA COMPLETA"


def validate_batavia_completa(apps, schema_editor):
    Producto = apps.get_model("mainApp", "Producto")
    product = Producto.objects.filter(pk=PRODUCT_ID).first()
    if product is None:
        return

    current_name = str(product.nombre or "").strip()
    if current_name.casefold() != TARGET_NAME.casefold():
        raise RuntimeError(
            f"El producto destino {PRODUCT_ID} debe llamarse '{TARGET_NAME}', "
            f"pero actualmente se llama '{current_name}'."
        )


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0029_permission_catalog_cleanup"),
    ]

    operations = [
        migrations.RunPython(
            validate_batavia_completa,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
