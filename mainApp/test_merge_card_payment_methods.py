from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase

from .models import (
    ConceptoEgreso,
    Egreso,
    MetodoPago,
    PuntosPago,
    Rol,
    Sucursal,
    TurnoCaja,
    TurnoCajaMedio,
    Usuario,
)


class MergeCardPaymentMethodsMigrationTests(TestCase):
    def test_merge_preserves_catalog_expenses_and_turn_totals(self):
        role = Rol.objects.create(nombre="Cajero")
        user = Usuario.objects.create_user(
            nombreusuario="cajero-migracion",
            password="test",
            rolid=role,
        )
        branch = Sucursal.objects.create(nombre="Sucursal migración")
        point = PuntosPago.objects.create(
            sucursalid=branch,
            nombre="Caja migración",
        )
        turn = TurnoCaja.objects.create(puntopago=point, cajero=user)

        MetodoPago.objects.create(
            codigo="tarjeta",
            nombre="Tarjeta",
            activo=False,
            es_sistema=True,
            orden=40,
            version=2,
        )
        MetodoPago.objects.create(
            codigo="banco_caja_social",
            nombre="Banco Caja Social",
            activo=True,
            es_sistema=True,
            orden=50,
            version=5,
        )
        card_row = TurnoCajaMedio.objects.create(
            turno=turn,
            metodo="tarjeta",
            esperado=Decimal("100.00"),
            contado=Decimal("120.00"),
            diferencia=Decimal("20.00"),
        )
        TurnoCajaMedio.objects.create(
            turno=turn,
            metodo="banco_caja_social",
            esperado=Decimal("50.00"),
            contado=Decimal("40.00"),
            diferencia=Decimal("-10.00"),
        )
        concept = ConceptoEgreso.objects.create(
            nombre="PAGO PRUEBA",
            creado_por=user,
        )
        expense = Egreso.objects.create(
            concepto=concept,
            monto=Decimal("25.00"),
            medio_pago="banco_caja_social",
            registrado_por=user,
            registrado_por_nombre=user.nombreusuario,
        )

        migration = import_module(
            "mainApp.migrations.0035_merge_card_payment_methods"
        )
        migration.merge_card_payment_methods(
            apps,
            SimpleNamespace(connection=connection),
        )

        self.assertFalse(
            MetodoPago.objects.filter(codigo="banco_caja_social").exists()
        )
        combined = MetodoPago.objects.get(codigo="tarjeta")
        self.assertEqual(combined.nombre, "Tarjeta / Banco Caja Social")
        self.assertTrue(combined.activo)
        self.assertEqual(combined.version, 6)

        expense.refresh_from_db()
        self.assertEqual(expense.medio_pago, "tarjeta")

        turn_rows = TurnoCajaMedio.objects.filter(turno=turn)
        self.assertEqual(turn_rows.count(), 1)
        merged = turn_rows.get(pk=card_row.pk)
        self.assertEqual(merged.metodo, "tarjeta")
        self.assertEqual(merged.esperado, Decimal("150.00"))
        self.assertEqual(merged.contado, Decimal("160.00"))
        self.assertEqual(merged.diferencia, Decimal("10.00"))
