"""Installer worker. Structured, credential-free output; secrets arrive on stdin."""
import argparse
import json
import os
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def report(message, **values):
    print(json.dumps(dict(message=message, **values), ensure_ascii=False), flush=True)


def platform_check():
    if (os.name != 'nt' or sys.getwindowsversion().major < 10
            or platform.machine().upper() not in ('AMD64', 'X86_64') or sys.maxsize <= 2**32):
        raise RuntimeError('Se necesita Windows 10/11 x64. No se admiten Windows 7/8, 32 bits ni ARM.')
    if not (ROOT / 'windows-package.json').is_file():
        raise RuntimeError('Este asistente solo funciona en el paquete Windows, nunca en tu repositorio habitual.')


def check_connection(values):
    from windows_installer import configuration
    connection = configuration.validate(values)
    # Before Django, independently verify the TLS connection and role without a write.
    import psycopg2
    db = configuration.database(connection)
    with psycopg2.connect(host=db['HOST'], port=db['PORT'], dbname=db['NAME'],
            user=db['USER'], password=db['PASSWORD'], **db['OPTIONS']) as conn:
        conn.set_session(readonly=True)
        with conn.cursor() as cursor:
            cursor.execute('SELECT rolsuper, current_setting(\'server_version_num\')::int FROM pg_roles WHERE rolname = current_user')
            superuser, version = cursor.fetchone()
            if superuser:
                raise RuntimeError('Usa un usuario de aplicación sin privilegios de superusuario para esta instalación.')
            if version < 140000:
                raise RuntimeError('La base central necesita PostgreSQL 14 o superior para esta distribución.')
    configuration._pending = connection
    os.environ['DJANGO_SETTINGS_MODULE'] = 'NovaSoft.settings'
    import django
    django.setup()
    from mainApp.services.hybrid_activation_checks import inspect_source
    expected = inspect_source(usuario=values.get('pos_user'), caja=values.get('point'))
    if not expected['required_turn'] or not expected['turn_id']:
        raise RuntimeError('Primero abre un turno online con el usuario y caja que deseas vincular.')
    return connection, expected


def activate(values):
    from windows_installer import configuration
    from local_pos.integrated_config import config_path, load_config, register_location
    from local_pos.integrated_prepare import prepare
    from local_pos.runtime import save_json
    from windows_installer.engines import choose
    connection, expected = check_connection(values)
    if any(expected[key] != values.get('confirmed', {}).get(key) for key in ('user_id', 'point_id', 'turn_id')):
        raise RuntimeError('El usuario, caja o turno cambió. Comprueba de nuevo antes de vincular.')
    path = config_path(ROOT)
    pg_bin = ROOT.parent / 'postgresql' / 'bin'
    selected = choose(ROOT, path, pg_bin, report)
    if selected is None:
        return
    configuration.save(ROOT, connection)
    if path.exists():
        current = load_config(path, ROOT)
        if current.get('activation') == 'journal_preview':
            raise RuntimeError('Esta instalación ya está activada. Abre NovaPOS; no se sustituirá su copia.')
    else:
        report('Preparando una base privada NUEVA. No se usa ni modifica el servicio PostgreSQL del equipo.')
        prepare(ROOT, path, **selected)
        current = json.loads(path.read_text(encoding='utf-8'))
        current['activation'] = 'database_only'
        save_json(path, current)
        register_location(path, ROOT)
    # Activation has a different Django profile; use a fresh process, with no credentials on argv.
    import subprocess
    env = dict(os.environ)
    env.pop('DJANGO_SETTINGS_MODULE', None)
    command = [sys.executable, '-B', str(ROOT / 'scripts/activate_integrated.py'),
               '--config', str(path), '--pg-bin', str(pg_bin), '--usuario-id', str(expected['user_id']),
               '--caja-id', str(expected['point_id']), '--turno-id', str(expected['turn_id']), '--apply']
    report('Descargando la copia autorizada. La nube se consulta en modo de solo lectura.')
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=1200,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        # Do not expose raw process/SQL errors or credentials to GUI/logs.
        raise RuntimeError('La activación no terminó. Los datos y el respaldo se conservaron. Revisa conexión, turno y migraciones de la nube antes de reintentar.')
    report('Equipo vinculado. Ya puedes abrir NovaPOS.', done=True)


def serve():
    import _thread
    import runpy
    import threading
    import time
    from urllib.request import build_opener, ProxyHandler
    from urllib.error import URLError
    from local_pos.runtime import port_free
    from local_pos.integrated_config import config_path, load_config
    config = load_config(config_path(ROOT), ROOT)
    if config.get('activation') != 'journal_preview':
        raise RuntimeError('Primero termina la vinculación de usuario, caja y turno.')
    port_free(8000)
    stop = threading.Event()

    def wait_for_stop():
        for line in sys.stdin:
            if line.strip() == 'STOP':
                stop.set()
                _thread.interrupt_main()
                return
        # Closing the launcher unexpectedly must not leave an orphan POS/engine.
        stop.set()
        _thread.interrupt_main()

    def ready():
        opener = build_opener(ProxyHandler({}))
        for _ in range(120):
            if stop.wait(1):
                return
            try:
                with opener.open('http://127.0.0.1:8000/', timeout=2) as response:
                    if response.status == 200:
                        report('NovaPOS iniciado. Mantén esta ventana abierta.', ready=True)
                        return
            except (URLError, OSError):
                pass
        report('El servidor tarda en responder. Revisa el estado; no se creó otra instancia.')

    threading.Thread(target=wait_for_stop, daemon=True).start()
    threading.Thread(target=ready, daemon=True).start()
    sys.argv = [str(ROOT / 'manage.py'), 'runserver', '127.0.0.1:8000', '--noreload']
    os.environ.pop('DJANGO_SETTINGS_MODULE', None)
    os.chdir(ROOT)
    report('Iniciando la aplicación habitual y su PostgreSQL privado…')
    runpy.run_path(str(ROOT / 'manage.py'), run_name='__main__')


def main():
    # Embedded Python ignores PYTHONUTF8/PYTHONIOENCODING environment flags.
    # Match the WinForms pipe decoder explicitly, including accented messages.
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('check', 'activate', 'serve', 'engine', 'wsl_prepare'))
    args = parser.parse_args()
    try:
        platform_check()
        if args.action == 'serve':
            serve()
        else:
            raw = sys.stdin.readline(32_001)
            if len(raw) > 32_000:
                raise RuntimeError('Configuración demasiado grande.')
            values = json.loads(raw)
            if args.action == 'check':
                _, expected = check_connection(values)
                report('Conexión verificada. Confirma usuario, caja y turno.', confirmed=expected)
            else:
                from local_pos.runtime import installation_lock, private_dir
                from local_pos.integrated_config import config_path
                from windows_installer.engines import choose, prepare_wsl, state_root
                # A separate setup lock does not populate the future business
                # directory and serializes multiple launcher windows/processes.
                with installation_lock(private_dir(state_root(ROOT))):
                    if args.action == 'engine':
                        if choose(ROOT, config_path(ROOT), ROOT.parent / 'postgresql/bin', report) is not None:
                            report('Motor seleccionado y comprobado. No se cambió ninguna base existente.', engine_ready=True)
                    elif args.action == 'wsl_prepare':
                        prepare_wsl(ROOT, config_path(ROOT), values, report)
                    else:
                        activate(values)
    except KeyboardInterrupt:
        report('NovaPOS detenido. Los datos locales se conservaron.')
    except Exception as exc:
        # ONLY intentional RuntimeErrors or sanitized DB diagnoses are shown.
        message = str(exc) if isinstance(exc, RuntimeError) else 'La operación no terminó (' + type(exc).__name__ + '). No se eliminaron datos ni se modificaron protecciones.'
        if args.action == 'check' and not isinstance(exc, RuntimeError):
            from mainApp.services.hybrid_activation_checks import database_failure_reason
            message = database_failure_reason(exc)
        report(message, error=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
