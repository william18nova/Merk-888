from django.db import migrations
from django.utils import timezone


TARGET_CODE = "tarjeta"
TARGET_LABEL = "Tarjeta / Banco Caja Social"
LEGACY_CODES = ("banco_caja_social", "caja_social", "bcs")


def _existing_tables(connection):
    with connection.cursor() as cursor:
        return set(connection.introspection.table_names(cursor))


def _canonicalize_column(connection, table_name, column_name, tables):
    if table_name not in tables:
        return

    quote = connection.ops.quote_name
    aliases = ", ".join(["%s"] * len(LEGACY_CODES))
    sql = (
        f"UPDATE {quote(table_name)} "
        f"SET {quote(column_name)} = %s "
        f"WHERE LOWER(TRIM({quote(column_name)})) IN ({aliases})"
    )
    with connection.cursor() as cursor:
        cursor.execute(sql, [TARGET_CODE, *LEGACY_CODES])


def _merge_turn_payment_rows(connection, tables):
    table_name = "turno_caja_medios"
    if table_name not in tables:
        return

    quote = connection.ops.quote_name
    aliases = ", ".join(["%s"] * (len(LEGACY_CODES) + 1))
    temp_table = "merge_card_turn_rows_0035"
    qualified_table = quote(table_name)
    qualified_temp = quote(temp_table)
    method_column = quote("metodo")

    # La tabla de turnos es grande y la base puede ser remota. Se agrupa todo
    # en SQL para evitar una consulta por turno y mantener la migración rápida.
    with connection.cursor() as cursor:
        cursor.execute(f"DROP TABLE IF EXISTS {qualified_temp}")
        try:
            cursor.execute(
                f"CREATE TEMPORARY TABLE {qualified_temp} AS "
                f"SELECT {quote('turno_id')} AS {quote('turno_id')}, "
                f"COALESCE("
                f"MIN(CASE WHEN LOWER(TRIM({method_column})) = %s "
                f"THEN {quote('id')} END), MIN({quote('id')})"
                f") AS {quote('keep_id')}, "
                f"COALESCE(SUM({quote('esperado')}), 0) AS {quote('esperado')}, "
                f"CASE WHEN COUNT({quote('contado')}) > 0 "
                f"THEN SUM({quote('contado')}) ELSE NULL END AS {quote('contado')}, "
                f"CASE WHEN COUNT({quote('contado')}) > 0 "
                f"THEN COALESCE(SUM({quote('contado')}), 0) "
                f"- COALESCE(SUM({quote('esperado')}), 0) "
                f"ELSE COALESCE(SUM({quote('diferencia')}), 0) END "
                f"AS {quote('diferencia')} "
                f"FROM {qualified_table} "
                f"WHERE LOWER(TRIM({method_column})) IN ({aliases}) "
                f"GROUP BY {quote('turno_id')}",
                [TARGET_CODE, TARGET_CODE, *LEGACY_CODES],
            )
            cursor.execute(
                f"DELETE FROM {qualified_table} "
                f"WHERE {quote('id')} IN ("
                f"SELECT row_to_remove.{quote('id')} "
                f"FROM {qualified_table} row_to_remove "
                f"INNER JOIN {qualified_temp} grouped_rows "
                f"ON grouped_rows.{quote('turno_id')} "
                f"= row_to_remove.{quote('turno_id')} "
                f"WHERE row_to_remove.{quote('id')} "
                f"<> grouped_rows.{quote('keep_id')}"
                f")"
            )
            cursor.execute(
                f"UPDATE {qualified_table} SET "
                f"{method_column} = %s, "
                f"{quote('esperado')} = ("
                f"SELECT grouped_rows.{quote('esperado')} "
                f"FROM {qualified_temp} grouped_rows "
                f"WHERE grouped_rows.{quote('keep_id')} = {qualified_table}.{quote('id')}"
                f"), {quote('contado')} = ("
                f"SELECT grouped_rows.{quote('contado')} "
                f"FROM {qualified_temp} grouped_rows "
                f"WHERE grouped_rows.{quote('keep_id')} = {qualified_table}.{quote('id')}"
                f"), {quote('diferencia')} = ("
                f"SELECT grouped_rows.{quote('diferencia')} "
                f"FROM {qualified_temp} grouped_rows "
                f"WHERE grouped_rows.{quote('keep_id')} = {qualified_table}.{quote('id')}"
                f") WHERE {quote('id')} IN ("
                f"SELECT {quote('keep_id')} FROM {qualified_temp}"
                f")",
                [TARGET_CODE],
            )
        finally:
            cursor.execute(f"DROP TABLE IF EXISTS {qualified_temp}")


def merge_card_payment_methods(apps, schema_editor):
    connection = schema_editor.connection
    database_alias = connection.alias
    tables = _existing_tables(connection)
    MetodoPago = apps.get_model("mainApp", "MetodoPago")

    manager = MetodoPago.objects.using(database_alias)
    target = manager.filter(codigo=TARGET_CODE).first()
    legacy_rows = list(manager.filter(codigo__in=LEGACY_CODES))
    all_rows = ([target] if target is not None else []) + legacy_rows

    active = any(row.activo for row in all_rows) if all_rows else True
    order = min((row.orden for row in all_rows), default=40)
    version = max((row.version for row in all_rows), default=0) + 1
    updated_by = next(
        (row.actualizado_por_id for row in all_rows if row.actualizado_por_id),
        None,
    )

    if target is None:
        target = manager.create(
            codigo=TARGET_CODE,
            nombre=TARGET_LABEL,
            activo=active,
            es_efectivo=False,
            es_sistema=True,
            orden=order,
            version=version,
            actualizado_por_id=updated_by,
            actualizado_por_nombre="Migración 0035",
        )
    else:
        target.nombre = TARGET_LABEL
        target.activo = active
        target.es_efectivo = False
        target.es_sistema = True
        target.orden = order
        target.version = version
        target.actualizado_en = timezone.now()
        target.actualizado_por_nombre = "Migración 0035"
        target.save(
            update_fields=[
                "nombre",
                "activo",
                "es_efectivo",
                "es_sistema",
                "orden",
                "version",
                "actualizado_en",
                "actualizado_por_nombre",
            ]
        )

    _canonicalize_column(connection, "ventas", "mediopago", tables)
    _canonicalize_column(connection, "venta_pagos", "metodo", tables)
    _canonicalize_column(connection, "egresos", "medio_pago", tables)
    _canonicalize_column(connection, "venta_reintegros", "medio_pago", tables)
    _merge_turn_payment_rows(connection, tables)

    manager.filter(codigo__in=LEGACY_CODES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("mainApp", "0034_operational_expenses"),
    ]

    operations = [
        migrations.RunPython(
            merge_card_payment_methods,
            migrations.RunPython.noop,
        ),
    ]
