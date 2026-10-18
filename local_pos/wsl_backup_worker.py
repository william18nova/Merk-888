"""Linux-only logical backups. Never restores SQL or starts a database."""
import json
import os
from pathlib import Path
import subprocess
import sys

from wsl_postgres_worker import paths


def main():
    os.umask(0o077)
    raw = sys.stdin.buffer.readline(32769)
    if len(raw) > 32768:
        raise RuntimeError('Configuración de respaldo demasiado grande.')
    config = json.loads(raw)
    _, binary = paths(config)  # Checks Linux owner, private cluster and its identity.
    if (config['user'] != 'nova_integrated_local' or config['database'] != 'nova_integrated_local'
            or type(config['port']) is not int or not 1024 <= config['port'] <= 65535):
        raise RuntimeError('Destino de respaldo local inválido.')
    action = sys.argv[1]
    if action in ('pg_dump_version', 'pg_restore_version'):
        return subprocess.run([binary / action.removesuffix('_version'), '--version']).returncode
    archive = Path(config['archive'])
    if (not archive.is_absolute() or archive.name != 'database.dump.partial'
            or any(p.is_symlink() for p in (archive, *archive.parents))):
        raise RuntimeError('Archivo de respaldo inválido.')
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PG')}
    env.update(PGHOST='127.0.0.1', PGPORT=str(config['port']), PGDATABASE=config['database'],
               PGUSER=config['user'], PGPASSWORD=config['password'], PGCONNECT_TIMEOUT='5',
               PGSSLMODE='disable', PGAPPNAME='nova-wsl-readonly-backup',
               PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=120000 -c lock_timeout=5000')
    if action == 'dump':
        with archive.open('xb') as output:
            return subprocess.run([binary / 'pg_dump', '--format=custom', '--no-password',
                '--lock-wait-timeout=5s'], env=env, stdin=subprocess.DEVNULL, stdout=output, timeout=270).returncode
    if action == 'list':
        return subprocess.run([binary / 'pg_restore', '--list', archive], stdin=subprocess.DEVNULL, timeout=25).returncode
    if action == 'decode':
        return subprocess.run([binary / 'pg_restore', '--file', '/dev/null', archive], stdin=subprocess.DEVNULL, timeout=110).returncode
    raise RuntimeError('Operación no permitida. Este programa nunca restaura ni ejecuta SQL.')


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('Respaldo Linux incompleto (' + type(exc).__name__ + '). No se restauró ni borró ninguna base.', file=sys.stderr)
        sys.exit(1)
