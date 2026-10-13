"""Smoke the distributed runtime in a NEW scratch directory. No cloud access."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def run(args):
    package = args.package.resolve()
    scratch = args.scratch.resolve()
    if scratch.exists():
        raise RuntimeError('La carpeta de prueba debe ser nueva; no se borrará nada.')
    scratch.mkdir(parents=True)
    python = package / 'python/python.exe'
    app = package / 'app'
    env = {k:v for k,v in os.environ.items() if not k.startswith(('NOVA_', 'PG', 'PYTHON'))
           and k not in {'DJANGO_SETTINGS_MODULE','DATABASE_URL','RUN_MAIN'}}
    env['PYTHONUTF8'] = '1'
    results = {}
    def execute(name, code, timeout=90):
        result = subprocess.run([str(python), '-B', '-c', code], cwd=app, env=env,
            text=True, encoding='utf-8', errors='replace', capture_output=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW)
        results[name] = {'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr}
        print(name + ': ' + ('OK' if result.returncode == 0 else 'FAILED'), flush=True)
        if result.returncode:
            print(result.stderr[-3500:], flush=True)
        return result.returncode == 0

    execute('imports', 'import django, psycopg2, win32crypt, dal, widget_tweaks; print(django.get_version()); '
            's=b"fictitious-smoke-password"; encrypted=win32crypt.CryptProtectData(s,"test",None,None,None,1); '
            'assert s not in encrypted; assert win32crypt.CryptUnprotectData(encrypted,None,None,None,1)[1]==s; print("DPAPI OK")')
    execute('package_integrity', '''
from pathlib import Path
import hashlib,json
root=Path.cwd().parent
manifest=json.loads((root/'app/windows-package.json').read_text())
for name,expected in manifest['files'].items():
    with (root/name).open('rb') as stream:
        assert hashlib.file_digest(stream,'sha256').hexdigest()==expected,name
for name in manifest['files']:
    assert not any(part.startswith('.') for part in Path(name).parts),name
    assert not name.endswith(('.sqlite3','.dump','.env','.log')),name
assert not (root/'app/.novapos-runtime.json').exists()
print(len(manifest['files']),'files verified; no copied device identity')
''')
    execute('django_and_templates_no_database', '''
import os
from pathlib import Path
from windows_installer import configuration
configuration._pending={'host':'never-connect.invalid','port':1,'database':'fictitious','user':'fictitious',
 'password':'fictitious-not-real','ca':'fictitious-ca.pem'}
os.environ['DJANGO_SETTINGS_MODULE']='NovaSoft.settings'
import django
django.setup()
from django.db.backends.base.base import BaseDatabaseWrapper
def forbidden(*args,**kwargs): raise AssertionError('Unexpected database connection')
BaseDatabaseWrapper.ensure_connection=forbidden
from django.core.management import call_command
call_command('check')
from django.template.loader import get_template
templates=list((Path.cwd()/'mainApp/templates').rglob('*.html'))
for template in templates: get_template(str(template.relative_to(Path.cwd()/'mainApp/templates')))
from local_pos.integrated_assets import load_assets
load_assets(Path.cwd())
print(len(templates),'templates and offline assets OK; zero DB connections')
''')
    if args.native_postgres:
        # Explicit isolated local engine, never the configured development copy.
        execute('native_postgres_plpgsql_and_schema', '''
from pathlib import Path
from local_pos.integrated_prepare import prepare
from local_pos.runtime import Postgres,installation_lock
import psycopg2
project=Path.cwd()
path=Path(%r)/'private/config.json'
config=prepare(project,path,project.parent/'postgresql/bin',port=55468)
with installation_lock(path.parent):
    engine=Postgres(path.parent,config['pg_bin'],config)
    engine.start()
    try:
        with psycopg2.connect(host='127.0.0.1',port=config['port'],dbname=config['database'],
             user=config['user'],password=config['password'],connect_timeout=3) as conn:
            with conn.cursor() as c:
                c.execute('CREATE TEMP TABLE smoke_installer (value integer)')
                c.execute('INSERT INTO smoke_installer VALUES (42)')
                c.execute('SELECT value FROM smoke_installer')
                assert c.fetchone()==(42,)
                c.execute('DO $$ BEGIN PERFORM 1; END $$')
                c.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
                print('Read/write and PL/pgSQL OK;',c.fetchone()[0],'local tables')
    finally: engine.stop()
''' % str(scratch), timeout=240)
    (scratch / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
    return 0 if all(r['returncode']==0 for r in results.values()) else 1


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--scratch',type=Path,required=True)
    parser.add_argument('--native-postgres',action='store_true')
    sys.exit(run(parser.parse_args()))
