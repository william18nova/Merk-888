from django.db import migrations


PRODUCT_ID = 2942
TARGET_NAME = "FR BATAVIA COMPLETA"
KNOWN_PREVIOUS_NAMES = {
    "fr batavia x und",
    "fr batavia media x und",
    TARGET_NAME.casefold(),
}


def rename_batavia_completa(apps, schema_editor):
    Producto = apps.get_model("mainApp", "Producto")
    product = Producto.objects.filter(pk=PRODUCT_ID).first()
    if product is None:
        return

    current_name = str(product.nombre or "").strip()
    if current_name.casefold() not in KNOWN_PREVIOUS_NAMES:
        raise RuntimeError(
            f"No se renombro el producto {PRODUCT_ID}: "
            f"su nombre actual es '{current_name}' y no corresponde a Batavia."
        )

    conflict = (
        Producto.objects.exclude(pk=PRODUCT_ID)
        .filter(nombre__iexact=TARGET_NAME)
        .first()
    )
    if conflict is not None:
        raise RuntimeError(
            f"No se renombro el producto {PRODUCT_ID}: "
            f"el nombre '{TARGET_NAME}' ya pertenece al producto {conflict.pk}."
        )

    if current_name != TARGET_NAME:
        product.nombre = TARGET_NAME
        product.save(update_fields=["nombre"])


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0029_permission_catalog_cleanup"),
    ]

    operations = [
        migrations.RunPython(
            rename_batavia_completa,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
