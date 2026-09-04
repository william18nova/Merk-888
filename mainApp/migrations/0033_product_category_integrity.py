import unicodedata

import django.db.models.deletion
from django.db import migrations, models


UNCATEGORIZED_NAME = "Sin categoría"


def _category_key(value):
    text = unicodedata.normalize("NFKD", str(value or "").strip())
    text = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return " ".join(text.split()).casefold()


def assign_uncategorized_before_constraint(apps, schema_editor):
    Categoria = apps.get_model("mainApp", "Categoria")
    Producto = apps.get_model("mainApp", "Producto")
    db_alias = schema_editor.connection.alias

    categories = list(
        Categoria.objects.using(db_alias).order_by("categoriaid")
    )
    exact = next(
        (category for category in categories if category.nombre == UNCATEGORIZED_NAME),
        None,
    )
    normalized = next(
        (
            category
            for category in categories
            if _category_key(category.nombre) == _category_key(UNCATEGORIZED_NAME)
        ),
        None,
    )
    uncategorized = exact or normalized
    if uncategorized is None:
        uncategorized = Categoria.objects.using(db_alias).create(
            nombre=UNCATEGORIZED_NAME,
            descripcion="Productos pendientes de una clasificación más específica.",
        )
    elif exact is None:
        # Reutiliza el PK de la categoría histórica «Sin Categoria».
        uncategorized.nombre = UNCATEGORIZED_NAME
        uncategorized.save(using=db_alias, update_fields=["nombre"])

    Producto.objects.using(db_alias).filter(categoria__isnull=True).update(
        categoria_id=uncategorized.pk
    )


class Migration(migrations.Migration):
    # PostgreSQL no permite encadenar el UPDATE de RunPython y el ALTER TABLE
    # dentro de la misma transacción cuando existen eventos de trigger
    # pendientes. Cada operación necesita su propio límite de commit.
    atomic = False

    dependencies = [
        ("mainApp", "0032_classify_nequi_incoming_notifications"),
    ]

    operations = [
        migrations.RunPython(
            assign_uncategorized_before_constraint,
            migrations.RunPython.noop,
            atomic=True,
        ),
        migrations.AlterField(
            model_name="producto",
            name="categoria",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                to="mainApp.categoria",
            ),
        ),
    ]
