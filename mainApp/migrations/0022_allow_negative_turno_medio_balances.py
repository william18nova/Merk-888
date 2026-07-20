from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("mainApp", "0021_reintegroventa"),
    ]

    operations = [
        # Los campos representan el movimiento neto del turno. Una devolución
        # puede salir por un medio distinto al pago original y dejar esperado
        # o contado por debajo de cero (por ejemplo: venta Nequi y reintegro en
        # efectivo). Estos checks pertenecen al esquema legacy y no aparecen
        # en el estado histórico de migraciones, por eso se eliminan con SQL.
        migrations.RunSQL(
            sql="""
                ALTER TABLE IF EXISTS turno_caja_medios
                    DROP CONSTRAINT IF EXISTS turno_caja_medios_esperado_check;
                ALTER TABLE IF EXISTS turno_caja_medios
                    DROP CONSTRAINT IF EXISTS turno_caja_medios_contado_check;
            """,
            # Restaurarlos sería inseguro una vez existan saldos netos
            # negativos y reintroduciría el error contable corregido aquí.
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
