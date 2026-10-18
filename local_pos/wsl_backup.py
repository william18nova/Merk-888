"""Windows adapter for Linux-only pg_dump/pg_restore; no native PG executables."""
import json
from pathlib import Path
import subprocess

from .integrated_config import DATABASE
from .wsl_postgres import WslPostgres


class WslBackup:
    def __init__(self, config):
        if config['database'] != DATABASE or config['user'] != DATABASE:
            raise RuntimeError('El respaldo WSL solo admite la base privada integrada.')
        self.config = config
        engine = WslPostgres(config['data_dir'], config['pg_bin'], config)
        self.prefix = engine.prefix
        self.worker = self.linux_path(Path(__file__).with_name('wsl_backup_worker.py'))

    def linux_path(self, path):
        result = subprocess.run([*self.prefix, 'wslpath', '-a', '-u', str(Path(path).resolve())],
            capture_output=True, text=True, encoding='utf-8', timeout=30, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        value = result.stdout.strip()
        if not value.startswith('/') or '\n' in value:
            raise RuntimeError('No se pudo localizar el archivo de respaldo en WSL.')
        return value

    def run(self, action, archive=None, *, stderr=None, timeout=30):
        if action not in {'pg_dump_version', 'pg_restore_version', 'dump', 'list', 'decode'}:
            raise RuntimeError('Operación de respaldo WSL inválida.')
        payload = {key: self.config[key] for key in ('backend', 'instance_id', 'pg_bin', 'port',
                                                    'database', 'user', 'password')}
        if archive is not None:
            archive = Path(archive).absolute()
            allowed = (Path(self.config['data_dir']) / 'backups').resolve()
            if (archive.name != 'database.dump.partial' or not archive.resolve().is_relative_to(allowed)
                    or any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (archive, *archive.parents))):
                raise RuntimeError('El respaldo debe quedar en la carpeta privada de esta instalación.')
            payload['archive'] = self.linux_path(archive)
        return subprocess.run([*self.prefix, 'python3', '-B', self.worker, action],
            input=(json.dumps(payload) + '\n').encode('utf-8'),
            stdout=subprocess.DEVNULL if stderr is not None else subprocess.PIPE,
            stderr=stderr if stderr is not None else subprocess.PIPE,
            timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)
