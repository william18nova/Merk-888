"""Upgrade ONLY the private WSL copy for a selected additive schema change.

Never imports cloud settings, resets a database or applies historical migrations.
Default is inspection; --apply requires a verified full backup before any DDL.
--upgrade ptm-reader handles the four PTM reader tables, not closing-payment detail.
--upgrade ptm-pool adds the separate cash ledger and nullable link on PTM operations.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def previous_signature(project, upgrade='close-payments'):
    markers = {
        'close-progress': (b'class AvanceCierreCaja(', b'class OperacionPTM('),
        'close-payments': (b'class DesglosePagosCierre(', b'class AvanceCierreCaja('),
        'ptm-reader': (b'class CuentaLecturaPTM(', b'class ConteoCierrePTM('),
        'ptm-pool': (b'class LecturaDiariaPTM(', b'class ConteoCierrePTM('),
    }
    if upgrade not in markers:
        raise RuntimeError('Actualización local no reconocida.')
    digest = hashlib.sha256()
    for name in ('mainApp/models.py', 'mainApp/hybrid_models.py', 'local_pos/models.py'):
        data = (project / name).read_bytes().replace(b'\r\n', b'\n')
        if name == 'mainApp/models.py':
            start = data.index(markers[upgrade][0])
            end = data.index(markers[upgrade][1], start)
            data = data[:start] + data[end:]
            if upgrade == 'ptm-pool':
                field = b'    bolsa = models.ForeignKey("BolsaEfectivoPTM", on_delete=models.PROTECT, null=True, blank=True)\n'
                if data.count(field) != 1:
                    raise RuntimeError('Cambió el enlace PTM revisado; no se actualizará la firma.')
                data = data.replace(field, b'')
        digest.update(name.encode())
        digest.update(data)
    return digest.hexdigest()


BACKUP_CODE = r'''
import hashlib,json,os,pathlib,subprocess,sys
os.umask(0o077)
p=json.load(sys.stdin)
root=pathlib.Path(p['backend']['data_dir'])/'backups'/p['backup_id']
root.mkdir(parents=True,exist_ok=False)
archive=root/'database.dump'
env={k:v for k,v in os.environ.items() if not k.startswith('PG')}
env.update(PGHOST='127.0.0.1',PGPORT=str(p['port']),PGDATABASE=p['database'],
    PGUSER=p['user'],PGPASSWORD=p['password'],PGCONNECT_TIMEOUT='5')
binary=pathlib.Path(p['pg_bin'])
with (root/'backup.log').open('wb') as log:
    subprocess.run([str(binary/'pg_dump'),'--format=custom','--no-password','--file',str(archive)],env=env,stderr=log,check=True,timeout=300)
    subprocess.run([str(binary/'pg_restore'),'--file','/dev/null',str(archive)],stdout=subprocess.DEVNULL,stderr=log,check=True,timeout=300)
listing=subprocess.run([str(binary/'pg_restore'),'--list',str(archive)],capture_output=True,check=True,timeout=30).stdout
if b'TABLE DATA' not in listing: raise RuntimeError('Incomplete backup')
h=hashlib.sha256()
with archive.open('rb') as stream:
    while block:=stream.read(1048576): h.update(block)
print(json.dumps({'file':str(archive),'bytes':archive.stat().st_size,'sha256':h.hexdigest(),'verified':True}))
'''


def table_counts(connection):
    from psycopg2 import sql
    with connection.cursor() as cursor:
        tables = sorted(connection.introspection.table_names(cursor))
        result = {}
        for table in tables:
            cursor.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table)))
            result[table] = cursor.fetchone()[0]
    return result


def apply_table(connection, upgrade='close-payments'):
    from mainApp.models import (AvanceCierreCaja, DesglosePagosCierre, CuentaLecturaPTM, LectorPTM,
                                MovimientoLeidoPTM, AuditoriaLecturaPTM, LecturaDiariaPTM,
                                BolsaEfectivoPTM, AjusteBolsaPTM, CorteBolsaPTM,
                                AuditoriaBolsaPTM, ParticipacionBolsaPTM, OperacionPTM)
    upgrades = {
        'close-progress': [AvanceCierreCaja],
        'close-payments': [DesglosePagosCierre],
        'ptm-reader': [CuentaLecturaPTM, LectorPTM, MovimientoLeidoPTM, AuditoriaLecturaPTM],
        'ptm-pool': [LecturaDiariaPTM, BolsaEfectivoPTM, AjusteBolsaPTM, CorteBolsaPTM,
                     AuditoriaBolsaPTM, ParticipacionBolsaPTM],
    }
    if upgrade not in upgrades:
        raise RuntimeError('Actualización local no reconocida.')
    before = table_counts(connection)
    with connection.schema_editor() as editor:
        for model in upgrades[upgrade]:
            table = model._meta.db_table
            if table not in before:
                editor.create_model(model)
            with connection.cursor() as cursor:
                columns = connection.introspection.get_table_description(cursor, table)
            if {col.name for col in columns} != {field.column for field in model._meta.fields}:
                raise RuntimeError('La tabla local no coincide; se conserva el bloqueo.')
        if upgrade == 'ptm-pool':
            field = OperacionPTM._meta.get_field('bolsa')
            with connection.cursor() as cursor:
                columns = connection.introspection.get_table_description(cursor, OperacionPTM._meta.db_table)
            if field.column not in {col.name for col in columns}:
                editor.add_field(OperacionPTM, field)
            with connection.cursor() as cursor:
                columns = connection.introspection.get_table_description(cursor, OperacionPTM._meta.db_table)
            if {col.name for col in columns} != {f.column for f in OperacionPTM._meta.fields}:
                raise RuntimeError('La tabla PTM no coincide; no se cambia la firma local.')
        after = table_counts(connection)
        if any(after.get(name) != count for name, count in before.items()):
            raise RuntimeError('Cambió el conteo de filas existentes; no se actualizará la configuración.')
    return len(before), sum(before.values())


def run(apply=False, upgrade='close-payments'):
    from local_pos.integrated_config import config_path, load_config, schema_signature, local_database
    from local_pos.runtime import installation_lock, private_dir, read_json, save_json
    from local_pos.wsl_postgres import WslPostgres
    path = config_path(ROOT)
    current = schema_signature(ROOT)
    old = read_json(path).get('schema_signature')
    if old == current:
        load_config(path, ROOT)
        print('La copia local ya tiene la firma actual. No se modificó nada.')
        return
    expected = previous_signature(ROOT, upgrade)
    config = load_config(path, ROOT, expected_schema=expected)
    if config.get('backend', {}).get('kind') != 'wsl':
        raise RuntimeError('Este actualizador solo admite la instalación WSL revisada.')
    labels = {'close-progress': 'únicamente la tabla de respaldo del cierre, sin mover dinero.',
              'ptm-reader': 'cuatro tablas del lector PTM.', 'ptm-pool': 'seis tablas de bolsa PTM y un enlace opcional, sin mover dinero.',
              'close-payments': 'únicamente la tabla DesglosePagosCierre.'}
    print('Cambio comprobado: ' + labels[upgrade])
    if not apply:
        print('Solo revisión. Usa --apply para respaldar y actualizar la copia local.')
        return
    with installation_lock(path.parent):
        config = load_config(path, ROOT, expected_schema=expected)
        postgres = WslPostgres(path.parent, config['pg_bin'], config)
        started = False
        connection = None
        try:
            postgres.start()
            started = True
            backup_id = upgrade + '-' + uuid4().hex
            private = private_dir(path.parent / 'backups' / backup_id)
            save_json(private / 'config-before.json', config)
            result = subprocess.run([*postgres.prefix, 'python3', '-c', BACKUP_CODE],
                input=json.dumps({key: config[key] for key in ('backend', 'pg_bin', 'port', 'database', 'user', 'password')} | {'backup_id': backup_id}), text=True,
                encoding='utf-8', capture_output=True, timeout=660)
            if result.returncode:
                raise RuntimeError('No se verificó el respaldo. No se modificó el esquema ni su firma.')
            report = json.loads(result.stdout)
            if not report.get('verified') or report.get('bytes', 0) <= 0:
                raise RuntimeError('Respaldo incompleto; actualización cancelada.')
            save_json(private / 'backup.json', report)
            print('Respaldo completo verificado: ' + report['file'], flush=True)
            os.environ.pop('DJANGO_SETTINGS_MODULE', None)
            from django.conf import settings
            settings.configure(SECRET_KEY=config['secret_key'], INSTALLED_APPS=[
                'django.contrib.auth', 'django.contrib.contenttypes', 'mainApp', 'local_pos'],
                AUTH_USER_MODEL='mainApp.Usuario', DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
                HYBRID_LOCAL_ENABLED=True, USE_TZ=True, TIME_ZONE='America/Bogota',
                DATABASES={'default': local_database(config)})
            import django
            django.setup()
            from django.db import connection
            tables, rows = apply_table(connection, upgrade)
            if schema_signature(ROOT) != current:
                raise RuntimeError('El código cambió durante la actualización; no se publicará la nueva firma.')
            config['schema_signature'] = current
            save_json(path, config)
            load_config(path, ROOT)
            save_json(private / 'result.json', {'complete': True, 'preserved_tables': tables,
                'preserved_rows': rows, 'schema_signature': current})
            print(f'OK: {tables} tablas y {rows} filas existentes conservadas. Sin acceso a Aiven.')
            print('Ahora puedes ejecutar python manage.py runserver.')
        finally:
            if connection is not None:
                connection.close()
            if started:
                postgres.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--upgrade', choices=('close-payments', 'ptm-reader', 'ptm-pool', 'close-progress'), default='close-payments')
    try:
        args = parser.parse_args()
        run(args.apply, args.upgrade)
    except Exception as error:
        print(str(error) if isinstance(error, RuntimeError) else
              f'Actualización no confirmada ({type(error).__name__}). No borres la copia ni sus respaldos.', file=sys.stderr)
        sys.exit(1)
