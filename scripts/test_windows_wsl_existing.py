"""Verify the NEW package against ONLY the earlier isolated WSL test fixture."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main(args):
    package, fixture = args.package.resolve(), args.fixture.resolve()
    if not (fixture / 'result-resume.json').is_file() or not (fixture / 'private/engine/wsl/ownership.json').is_file():
        raise RuntimeError('This verifier only accepts the previous isolated installer test fixture.')
    if not re.fullmatch(r'verified-linux-backup(?:-[a-z0-9-]+)?\.json', args.report_name):
        raise RuntimeError('Use a simple verified-linux-backup report filename.')
    destination = fixture / args.report_name
    if destination.exists():
        raise RuntimeError('A test result already exists. Preserve it instead of overwriting.')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('NOVA_', 'PG', 'PYTHON'))
           and k not in {'DJANGO_SETTINGS_MODULE', 'DATABASE_URL', 'RUN_MAIN'}}
    code = '''
import sys,json
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
from pathlib import Path
from uuid import uuid4
import psycopg2
from windows_installer.engines import probe
from windows_installer.wsl_setup import ensure_owned
from local_pos.integrated_config import load_config
from local_pos.runtime import installation_lock
from local_pos.wsl_postgres import WslPostgres,wait_for_forwarding_release
from local_pos.integrated_activation import backup_local
fixture=Path(%r)
identity=json.loads((fixture/'private/engine/wsl/ownership.json').read_text())
config=load_config(fixture/'business/config.json',identity['project'])
assert config['backend']['distribution']==identity['name']
ensure_owned(identity['name'],fixture/'private/engine/wsl/distribution')
probe(config['pg_bin'],fixture/('relay-probe-'+uuid4().hex),port=config['port'],distribution=identity['name'])
print('PASS: Linux PL/pgSQL + write/read probe; Windows relay released before returning',flush=True)
for attempt in range(2):
    with installation_lock(Path(config['data_dir'])):
        engine=WslPostgres(config['data_dir'],config['pg_bin'],config)
        engine.start()
        try:
            with psycopg2.connect(host='127.0.0.1',port=config['port'],dbname=config['database'],
                 user=config['user'],password=config['password'],connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT value FROM installer_persistence_probe')
                    assert cursor.fetchall()==[(42,)],'This is not the expected fictitious fixture'
                    cursor.execute('CREATE TEMP TABLE installer_check (value integer)')
                    cursor.execute('DO $$ BEGIN INSERT INTO installer_check VALUES (123); END $$')
                    cursor.execute('SELECT value FROM installer_check')
                    assert cursor.fetchone()==(123,)
                    cursor.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
                    print('PASS: startup',attempt+1,'; persistence + PL/pgSQL SQL; tables',cursor.fetchone()[0],flush=True)
            if attempt==0:
                backup=backup_local(config,Path('NATIVE_CLIENTS_MUST_NOT_BE_USED'))
                result=json.loads((backup/'manifest.json').read_text())
                assert result['status']=='verified'
                print('PASS: backup, listing and full decode using ONLY Linux PostgreSQL tools',flush=True)
        finally: engine.stop()
    wait_for_forwarding_release(config['port'])
print('PASS: stop/restart; original test row preserved; test port released; no cloud connections',flush=True)
''' % str(fixture)
    result = subprocess.run([str(package / 'python/python.exe'), '-B', '-c', code],
        cwd=package / 'app', env=env, capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=360, creationflags=subprocess.CREATE_NO_WINDOW)
    destination.write_text(json.dumps({'returncode': result.returncode, 'stdout': result.stdout,
        'stderr': result.stderr}, indent=2, ensure_ascii=False), encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    print(result.stdout, result.stderr)
    return result.returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--report-name', default='verified-linux-backup.json')
    sys.exit(main(parser.parse_args()))
