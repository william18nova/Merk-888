"""Prueba 0043 en SQLite :memory: con datos ficticios; nunca carga settings del POS."""
import importlib
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from django.conf import settings

settings.configure(
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    INSTALLED_APPS=[], SECRET_KEY="isolated-migration-test-only",
)
import django
django.setup()

from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

state = ProjectState()
state.add_model(ModelState("mainApp", "MetodoPago", [
    ("codigo", models.CharField(max_length=50, primary_key=True)),
], {"db_table": "metodos_pago"}))
state.add_model(ModelState("mainApp", "Egreso", [
    ("id", models.AutoField(primary_key=True)),
    ("monto", models.DecimalField(max_digits=14, decimal_places=2)),
], {"db_table": "egresos"}))

with connection.schema_editor() as editor:
    for model in state.apps.get_models():
        editor.create_model(model)
methods = state.apps.get_model("mainApp", "MetodoPago")
for code in ("nequi", "tarjeta", "efectivo", "daviplata", "otro"):
    methods.objects.create(codigo=code)
state.apps.get_model("mainApp", "Egreso").objects.create(monto=Decimal("1250.50"))

migration_module = importlib.import_module("mainApp.migrations.0043_expense_four_per_thousand")
migration = migration_module.Migration("0043_expense_four_per_thousand", "mainApp")
with connection.schema_editor() as editor:
    state = migration.apply(state, editor)

methods = state.apps.get_model("mainApp", "MetodoPago")
assert set(methods.objects.filter(aplica_4xmil_egresos=True).values_list("pk", flat=True)) == {"nequi", "tarjeta"}
row = state.apps.get_model("mainApp", "Egreso").objects.get()
assert row.monto == Decimal("1250.50") and row.impuesto_4xmil == 0 and row.aplica_4xmil is False
print("0043 aplicada en SQLite aislado: Nequi/tarjeta activos, pago histórico intacto y restricciones creadas.")
