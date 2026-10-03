"""Verificación/migración aislada; no importa ajustes ni llama a APIs de la nube."""
import argparse
import hashlib
import json
from pathlib import Path


def snapshot(*, missing_tables=()):
    from django.apps import apps
    from django.db import connection, transaction
    from django.conf import settings
    from psycopg2 import sql
    from .models import LocalNode
    if (connection.settings_dict["HOST"] != "127.0.0.1"
            or connection.settings_dict["NAME"] != "nova_full_local_lab"):
        raise RuntimeError("La actualización solo admite la base local identificada.")
    if list(LocalNode.objects.values_list("pk", flat=True)) != [__import__("uuid").UUID(settings.LOCAL_CONFIG["instance_id"])]:
        raise RuntimeError("La identidad de la base no corresponde a esta instalación.")
    result = {}
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            tables = connection.introspection.table_names(cursor)
            expected = {m._meta.db_table: m for m in apps.get_models(include_auto_created=True)
                        if m._meta.managed and not m._meta.proxy}
            if set(tables) - set(expected):
                raise RuntimeError("Hay tablas locales no reconocidas; se requiere revisión antes de actualizar.")
            if set(expected) - set(tables) != set(missing_tables):
                raise RuntimeError("Faltan tablas locales o hay una migración parcial no reconocida.")
            for table in sorted(tables):
                fields = {field.column for field in expected[table]._meta.local_fields}
                description = connection.introspection.get_table_description(cursor, table)
                if fields != {field.name for field in description}:
                    raise RuntimeError("El esquema de una tabla local no coincide; no se aplicarán cambios genéricos.")
                schema = [(field.name, field.type_code, field.internal_size, field.null_ok, field.default)
                          for field in description]
                constraints = connection.introspection.get_constraints(cursor, table)
                digest = hashlib.sha256()
                cursor.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text COLLATE "C"').format(sql.Identifier(table)))
                count = 0
                while rows := cursor.fetchmany(500):
                    for (row,) in rows:
                        encoded = row.encode("utf-8")
                        digest.update(len(encoded).to_bytes(8, "big"))
                        digest.update(encoded)
                        count += 1
                result[table] = {"rows": count, "sha256": digest.hexdigest(),
                    "schema": hashlib.sha256(json.dumps([schema, constraints], sort_keys=True, default=str).encode()).hexdigest()}
    return result


def migrate(previous_version):
    from django.db import connection
    from .models import LocalOperator, LocalHandoff
    new_tables = {LocalOperator._meta.db_table, LocalHandoff._meta.db_table}
    before = snapshot(missing_tables=new_tables if previous_version == 1 else ())
    if previous_version == 1:
        if set(before) & new_tables:
            raise RuntimeError("El esquema anterior contiene una migración parcial no reconocida.")
        with connection.schema_editor(atomic=True) as editor:
            editor.create_model(LocalOperator)
            editor.create_model(LocalHandoff)
    elif previous_version != 2:
        raise RuntimeError("Migración local no admitida.")
    after = snapshot()
    if any(after.get(table) != contents for table, contents in before.items()):
        raise RuntimeError("La actualización cambió información anterior; debe recuperarse el respaldo.")
    if set(after) - set(before) != (new_tables if previous_version == 1 else set()):
        raise RuntimeError("La actualización creó tablas no previstas.")
    if any(after[name]["rows"] for name in set(after) - set(before)):
        raise RuntimeError("Las tablas nuevas no están vacías.")
    return after


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--from-version", type=int)
    args = parser.parse_args()
    from .runtime import django_setup, save_json, ROOT
    django_setup(args.root, initializing=True)
    from django.core.management import call_command
    from django.db import connections
    try:
        call_command("check", verbosity=0)
        result = migrate(args.from_version) if args.from_version is not None else snapshot()
        if args.from_version is not None:
            import shutil
            call_command("collectstatic", interactive=False, verbosity=0)
            shutil.copytree(ROOT / "offline_vendor", args.root / "staticfiles/local_vendor", dirs_exist_ok=True)
        save_json(args.output, result)
    finally:
        connections.close_all()


if __name__ == "__main__":
    main()
