"""First-install engine selection. Never replace an existing business copy."""
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from uuid import uuid4

from local_pos.integrated_config import load_config, private_config_path
from local_pos.runtime import Postgres, private_dir, port_free, save_json
from windows_installer.configuration import connection_path

PORT = 55460


class NativePolicyBlocked(RuntimeError):
    pass


def policy_blocked(exc):
    # A generic missing DLL, permissions, port conflict or a SQL error is NOT
    # evidence of an application-control block. Preserve those original causes.
    if isinstance(exc, OSError) and getattr(exc, 'winerror', None) == 4551:
        return True
    if isinstance(exc, subprocess.CalledProcessError):
        output = str(exc.stderr or '') + '\n' + str(exc.stdout or '')
        return bool(re.search(r'\b4551\b', output) and 'plpgsql' in output.lower())
    return False


def sql_probe(config, password):
    import psycopg2
    with psycopg2.connect(host='127.0.0.1', port=config['port'], dbname='postgres',
            user='nova_integrated_owner', password=password, connect_timeout=5,
            options='-c statement_timeout=10000') as conn:
        with conn.cursor() as cursor:
            cursor.execute('DO $$ BEGIN PERFORM 1; END $$')
            cursor.execute('CREATE TEMP TABLE nova_installer_probe (value integer)')
            cursor.execute('INSERT INTO nova_installer_probe VALUES (42)')
            cursor.execute('SELECT value FROM nova_installer_probe')
            if cursor.fetchone() != (42,):
                raise RuntimeError('La prueba de lectura/escritura local no coincidió.')


def probe(pg_bin, root, *, port=PORT, distribution=None):
    """A NEW disposable cluster only; no cloud settings, migrations or identities."""
    root = Path(root)
    if root.exists():
        raise RuntimeError('La prueba de motor exige una carpeta nueva; no se borrará nada.')
    port_free(port)
    private_dir(root)
    password = secrets.token_hex(48)
    identity = str(uuid4())
    config = dict(port=port, password=password, database='postgres', user='nova_integrated_owner',
                  instance_id=identity, pg_bin=str(pg_bin))
    if distribution:
        from local_pos.wsl_postgres import WslPostgres
        config['backend'] = dict(kind='wsl', distribution=distribution, user='novapos',
            data_dir='/home/novapos/.local/share/NovaPOS/integrated/' + identity)
        engine = WslPostgres(root, pg_bin, config)
    else:
        engine = Postgres(root, pg_bin, config)
    password_file = root / 'bootstrap-password'
    password_file.write_text(password, encoding='utf-8')
    started = False
    try:
        if distribution:
            engine.initialize(password)
            engine.start(initializing_password=password)
        else:
            engine.run('initdb', '-D', root / 'postgres', '-U', 'nova_integrated_owner',
                       '-A', 'scram-sha-256', '-E', 'UTF8', '--locale=C',
                       '--pwfile=' + str(password_file), capture=True)
            engine.start()
        started = True
        sql_probe(config, password)
        if not distribution:
            # Native pg_dump can be rejected even when postgres/plpgsql work.
            # Check backup tools BEFORE creating/binding the business copy.
            archive = root / 'probe.dump'
            engine.run('pg_dump', '-h', '127.0.0.1', '-p', port, '-U', 'nova_integrated_owner',
                       '-d', 'postgres', '--no-password', '--format=custom', '--file', archive, capture=True)
            engine.run('pg_restore', '--list', archive, capture=True)
            engine.run('pg_restore', '--file', os.devnull, archive, capture=True)
    except (subprocess.CalledProcessError, OSError) as exc:
        log = root / 'engine-error.txt'
        log.write_text(str(getattr(exc, 'stdout', '') or '') + '\n' +
                       str(getattr(exc, 'stderr', '') or '') + '\n' + str(exc), encoding='utf-8')
        if not distribution and policy_blocked(exc):
            raise NativePolicyBlocked('Windows bloqueó PostgreSQL (4551). Diagnóstico: ' + str(log)) from None
        raise RuntimeError('No pasó la prueba del motor. No se cambiará automáticamente a otra base. Diagnóstico: ' + str(log)) from None
    finally:
        password_file.unlink(missing_ok=True)
        if started:
            engine.stop()
            if distribution:
                # WSL's Windows localhost relay can outlive PostgreSQL briefly.
                # Do not report ready until a subsequent prepare can bind safely.
                from local_pos.wsl_postgres import wait_for_forwarding_release
                wait_for_forwarding_release(port)
    save_json(root / 'result.json', dict(plpgsql=True, read_write=True, port=port,
                                       kind='wsl' if distribution else 'native'))


def state_root(project):
    return connection_path(project).parent / 'engine'


def read_state(project):
    path = state_root(project) / 'selection.json'
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_size > 8192:
        raise RuntimeError('Estado de selección de motor inválido.')
    state = json.loads(path.read_text(encoding='utf-8'))
    if (state.get('format') != 'nova-engine-selection-v1'
            or state.get('project') != str(Path(project).resolve())
            or state.get('status') not in ('native_ready', 'native_blocked', 'wsl_ready')):
        raise RuntimeError('La elección de motor no pertenece a esta instalación.')
    return state


def record(project, status, **extra):
    root = private_dir(state_root(project))
    value = dict(format='nova-engine-selection-v1', project=str(Path(project).resolve()), status=status, **extra)
    save_json(root / 'selection.json', value)
    return value


def existing_copy(project, path):
    path = private_config_path(path, project)
    if path.exists():
        # Includes phase/schema/identity checks. An incomplete config never
        # causes a fallback to a new empty database, even without a locator.
        return load_config(path, project)
    if path.parent.exists() and any(path.parent.iterdir()):
        raise RuntimeError('Ya existen archivos locales. No se cambiará de motor ni se creará una base vacía.')
    return None


def choose(project, path, pg_bin, report):
    current = existing_copy(project, path)
    if current is not None:
        return current
    state = read_state(project)
    if state is None:
        root = private_dir(state_root(project))
        report('Probando PostgreSQL nativo, PL/pgSQL, lectura/escritura y respaldo en una base temporal nueva…')
        try:
            probe(pg_bin, root / ('native-probe-' + uuid4().hex))
        except NativePolicyBlocked as exc:
            state = record(project, 'native_blocked', reason=str(exc))
        else:
            state = record(project, 'native_ready')
    if state['status'] == 'native_blocked':
        report('Windows bloqueó el motor nativo (4551). Puedes autorizar el respaldo WSL2. No se creó la copia del negocio.',
               wsl_required=True)
        return None
    if state['status'] == 'wsl_ready':
        from windows_installer.wsl_setup import distribution_name
        return dict(pg_bin='/usr/lib/postgresql/16/bin', wsl_distribution=distribution_name(project), wsl_user='novapos')
    return dict(pg_bin=str(pg_bin))


def prepare_wsl(project, path, values, report):
    if values.get('wsl_consent') is not True:
        raise RuntimeError('Debes autorizar expresamente preparar WSL2.')
    if existing_copy(project, path) is not None:
        raise RuntimeError('Ya existe una copia preparada: no se cambiará su motor.')
    state = read_state(project)
    if not state or state['status'] not in ('native_blocked', 'wsl_ready'):
        raise RuntimeError('El respaldo WSL2 solo se ofrece después de comprobar el bloqueo nativo 4551.')
    if state['status'] == 'wsl_ready':
        report('El respaldo WSL2 ya fue comprobado. Vuelve a comprobar y vincular la caja.', engine_ready=True)
        return
    from windows_installer.wsl_setup import provision, WslUnavailable
    try:
        distribution = provision(project, report)
    except WslUnavailable as exc:
        report(str(exc), wsl_install_required=True)
        return
    report('Verificando PostgreSQL Linux desde Windows: PL/pgSQL y lectura/escritura…')
    probe('/usr/lib/postgresql/16/bin', state_root(project) / ('wsl-probe-' + uuid4().hex), distribution=distribution)
    record(project, 'wsl_ready')
    report('WSL2 funciona y quedó seleccionado. Comprueba de nuevo usuario y caja para vincular.', engine_ready=True)
