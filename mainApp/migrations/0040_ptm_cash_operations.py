from decimal import Decimal
import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def crear_productos_ptm(apps, schema_editor):
    Producto = apps.get_model("mainApp", "Producto")
    Categoria = apps.get_model("mainApp", "Categoria")
    products = Producto.objects.using(schema_editor.connection.alias)
    categories = Categoria.objects.using(schema_editor.connection.alias)
    old = list(products.filter(nombre__iexact="PTM"))
    if len(old) > 1:
        raise RuntimeError("Hay varios productos llamados PTM. Revisa cuál debe conservarse antes de migrar.")
    old = old[0] if old else None
    category = old.categoria if old else categories.filter(nombre__iexact="Servicios PTM").first()
    if category is None:
        category = categories.create(nombre="Servicios PTM")
    for kind, name in [("recarga", "PTM RECARGA O PAGOS"), ("retiro", "PTM RETIROS")]:
        matches = list(products.filter(nombre__iexact=name))
        if len(matches) > 1:
            raise RuntimeError(f"Hay varios productos llamados {name}. Revisa el catálogo antes de migrar.")
        product = matches[0] if matches else None
        if kind == "recarga" and old:
            if product and product.pk != old.pk:
                raise RuntimeError("Existen PTM y PTM RECARGA O PAGOS con IDs diferentes. No se fusionaron automáticamente.")
            product = old
        if product:
            products.filter(pk=product.pk).update(nombre=name, tipo_ptm=kind, precio=Decimal("0.00"))
        else:
            products.create(nombre=name, tipo_ptm=kind, categoria=category, precio=Decimal("0.00"),
                            descripcion="Operación de efectivo PTM. Registrar desde Caja → Operaciones PTM.")


class Migration(migrations.Migration):
    dependencies = [("mainApp", "0039_telegram_transcription_fallback")]
    operations = [
        migrations.AddField(
            model_name="producto", name="tipo_ptm",
            field=models.CharField(blank=True, choices=[("retiro", "PTM RETIROS"), ("recarga", "PTM RECARGA O PAGOS")],
                                   editable=False, max_length=10, null=True, unique=True),
        ),
        migrations.CreateModel(
            name="OperacionPTM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("retiro", "Retiro"), ("recarga", "Recarga o pago")], max_length=10)),
                ("monto", models.DecimalField(decimal_places=2, max_digits=12)),
                ("referencia", models.CharField(max_length=100, unique=True)),
                ("solicitud_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("producto", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="mainApp.producto")),
                ("turno", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="operaciones_ptm", to="mainApp.turnocaja")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "operaciones_ptm", "ordering": ["-creado_en", "-pk"], "constraints": [
                models.CheckConstraint(condition=models.Q(monto__gt=0), name="ptm_monto_positivo"),
                models.CheckConstraint(condition=models.Q(tipo__in=["retiro", "recarga"]), name="ptm_tipo_valido"),
                models.CheckConstraint(condition=~models.Q(referencia=""), name="ptm_referencia_obligatoria"),
            ]},
        ),
        migrations.CreateModel(
            name="ConteoCierrePTM",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("declarado", models.PositiveIntegerField()), ("registrado", models.PositiveIntegerField()),
                ("creado_en", models.DateTimeField(auto_now_add=True)),
                ("turno", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="conteos_ptm", to="mainApp.turnocaja")),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "conteos_cierre_ptm", "ordering": ["-creado_en", "-pk"]},
        ),
        # No reclasifica ventas anteriores ni modifica cierres históricos. No se
        # borran registros financieros al revertir un despliegue.
        migrations.RunPython(crear_productos_ptm),
    ]
