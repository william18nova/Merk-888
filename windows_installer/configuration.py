"""Per-Windows-user connection settings, encrypted by DPAPI, never in a package."""
import hashlib
import json
import os
from pathlib import Path
import secrets

_pending = None  # Only while the first-run worker validates an unsaved connection.


def connection_path(project):
    key = hashlib.sha256(os.path.normcase(str(Path(project).resolve())).encode()).hexdigest()[:16]
    return Path(os.environ['LOCALAPPDATA']) / 'NovaPOS' / 'connections' / key / 'connection.dpapi'


def validate(values):
    result = {}
    for name in ('host', 'database', 'user', 'password', 'ca'):
        value = values.get(name)
        if not isinstance(value, str) or not value or len(value) > 4096 or '\x00' in value:
            raise RuntimeError('Completa servidor, base, usuario, contraseña y certificado CA.')
        result[name] = value if name == 'password' else value.strip()
    host = result['host']
    if any(char in host for char in '/\\ @;\r\n') or not host:
        raise RuntimeError('Escribe solamente el nombre DNS del servidor, sin URL ni contraseña.')
    try:
        port = int(values.get('port', 0))
    except (ValueError, TypeError):
        port = 0
    if not 1 <= port <= 65535:
        raise RuntimeError('El puerto de la nube no es válido.')
    result['port'] = port
    ca = Path(result['ca'])
    if not ca.is_file() or ca.is_symlink() or ca.stat().st_size > 1_000_000:
        raise RuntimeError('Selecciona el certificado CA descargado del proveedor de la base de datos.')
    return result


def database(values):
    return {'ENGINE': 'django.db.backends.postgresql', 'HOST': values['host'],
            'PORT': values['port'], 'NAME': values['database'], 'USER': values['user'],
            'PASSWORD': values['password'], 'CONN_MAX_AGE': 0, 'CONN_HEALTH_CHECKS': True,
            'OPTIONS': {'sslmode': 'verify-full', 'sslrootcert': values['ca'],
                        'connect_timeout': 5}}


def save(project, values):
    from local_pos.runtime import private_dir
    import win32crypt
    path = connection_path(project)
    if path.exists():
        # A repeated activation may resume, but may NOT change database silently.
        old = load(project)
        if any(old[name] != values[name] for name in ('host', 'port', 'database', 'user', 'password')):
            raise RuntimeError('Este equipo ya tiene otra conexión. No se reemplazaron sus credenciales.')
        return
    root = private_dir(path.parent)
    ca = root / 'cloud-ca.pem'
    data = dict(values, ca=str(ca), secret_key=secrets.token_hex(48),
                format='nova-windows-connection-v1', project=str(Path(project).resolve()))
    # Certificate is public; the connection/password is protected for this Windows user.
    with ca.open('xb') as stream:
        stream.write(Path(values['ca']).read_bytes())
    encrypted = win32crypt.CryptProtectData(json.dumps(data).encode('utf-8'), 'NovaPOS', None, None, None, 1)
    with path.open('xb') as stream:
        stream.write(encrypted)
        stream.flush()
        os.fsync(stream.fileno())


def load(project):
    import win32crypt
    path = connection_path(project)
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 32_000:
        raise RuntimeError('Falta vincular este equipo. Abre Configurar NovaPOS.')
    try:
        raw = win32crypt.CryptUnprotectData(path.read_bytes(), None, None, None, 1)[1]
        data = json.loads(raw)
        if (data.get('format') != 'nova-windows-connection-v1'
                or Path(data['project']).resolve() != Path(project).resolve()
                or len(data['secret_key']) < 40):
            raise ValueError
        validate(data)
        return data
    except Exception:
        raise RuntimeError('No se pudo abrir la conexión privada de este usuario Windows. No copies la instalación de otro equipo.') from None


def apply(namespace):
    values = _pending or load(namespace['BASE_DIR'])
    namespace['DATABASES'] = {'default': database(values)}
    namespace['SECRET_KEY'] = values.get('secret_key') or secrets.token_hex(48)
    namespace['DEBUG'] = False
    namespace['ALLOWED_HOSTS'] = ['localhost', '127.0.0.1', '[::1]']
    namespace['CSRF_TRUSTED_ORIGINS'] = []
