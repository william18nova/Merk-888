"""Build a self-extracting x64 installer of the ORIGINAL integrated app.

Only explicit source/static files are packaged. No repository .env, local
configuration, database, history, dumps, caches, credentials or device identity.
The stage must be NEW; this builder never recursively deletes a destination.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SOURCE_DIRS = ('NovaSoft', 'mainApp', 'local_pos', 'pos_shared', 'hybrid_client', 'windows_installer')
EXTENSIONS = {'.py', '.html', '.css', '.js', '.json', '.png', '.jpg', '.jpeg', '.svg', '.gif',
              '.ico', '.woff', '.woff2', '.ttf', '.eot', '.txt', '.md', '.manifest', '.cs'}
SCRIPTS = ('activate_integrated.py', 'prepare_integrated_install.py', 'prepare_integrated_assets.py',
           'build_full_local_assets.py', 'update_integrated_close_payments.py')
REPLACED_SETTINGS = {'SECRET_KEY', 'DATABASES', 'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS',
                     'POS_AGENT_TOKEN', 'TELEGRAM_ENV_FILE'}


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verified_component(path):
    from scripts.download_windows_components import COMPONENTS, HOSTS
    from urllib.parse import urlsplit
    path = Path(path)
    if path.name not in COMPONENTS or path.is_symlink():
        raise RuntimeError('Componente desconocido; usa el descargador de proveedores oficiales.')
    manifest = json.loads((path.parent / 'components.json').read_text(encoding='utf-8'))
    record = manifest.get(path.name, {})
    source = urlsplit(record.get('source', ''))
    if (source.scheme != 'https' or source.hostname not in HOSTS
            or record.get('sha256') != digest(path)):
        raise RuntimeError('El componente no coincide con la descarga verificada; no se construyó el instalador.')


def settings_source(source):
    tree = ast.parse(source)
    secrets = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in REPLACED_SETTINGS for t in node.targets):
            names = {t.id for t in node.targets if isinstance(t, ast.Name)}
            # Record old literal credentials WITHOUT printing them. They must not survive packaging anywhere.
            if names & {'SECRET_KEY', 'DATABASES', 'POS_AGENT_TOKEN'}:
                if 'DATABASES' in names:
                    for sub in ast.walk(node.value):
                        if isinstance(sub, ast.Dict):
                            for key, value in zip(sub.keys, sub.values):
                                if isinstance(key, ast.Constant) and key.value == 'PASSWORD' and isinstance(value, ast.Constant):
                                    secrets.append(value.value)
                else:
                    secrets.extend(sub.value for sub in ast.walk(node.value) if isinstance(sub, ast.Constant)
                                   and isinstance(sub.value, str) and len(sub.value) >= 32)
            node.value = ast.parse('{}' if 'DATABASES' in names else '[]' if names & {'ALLOWED_HOSTS', 'CSRF_TRUSTED_ORIGINS'} else '""', mode='eval').body
    tree.body.extend(ast.parse('from windows_installer.configuration import apply as _apply_windows_connection\n_apply_windows_connection(globals())').body)
    return ast.unparse(ast.fix_missing_locations(tree)) + '\n', [s for s in secrets if isinstance(s, str) and s]


def eligible(path, root=ROOT):
    rel = path.relative_to(root)
    if path.is_symlink() or any(getattr(p, 'is_junction', lambda: False)() for p in (path, *path.parents)):
        raise RuntimeError('No se empaquetan enlaces ni junctions.')
    if any(part.startswith('.') or part in {'__pycache__', 'tests', 'fixtures', 'assets', 'node_modules', 'media'} for part in rel.parts):
        return False
    if path.name.startswith(('test_', 'smoke_')) or path.name in {'tests.py', 'debug.log'}:
        return False
    if path.suffix.lower() not in EXTENSIONS:
        return False
    if rel.parts[0] == 'NovaSoft' and path.name not in {'__init__.py', 'settings.py', 'urls.py', 'wsgi.py', 'asgi.py',
            'integrated_settings.py', 'integrated_activation_settings.py', 'integrated_schema_settings.py', 'wsl_development_settings.py'}:
        return False
    return True


def source_stage(destination, root=ROOT):
    destination, root = Path(destination), Path(root)
    if destination.exists():
        raise RuntimeError('El destino de código debe ser nuevo.')
    destination.mkdir(parents=True)
    settings, secret_values = settings_source((root / 'NovaSoft/settings.py').read_text(encoding='utf-8-sig'))
    files = [root / 'manage.py']
    for directory in SOURCE_DIRS:
        files.extend(p for p in (root / directory).rglob('*') if p.is_file() and eligible(p, root))
    files.extend(root / 'scripts' / name for name in SCRIPTS)
    # The preverified public UI libraries are the ONLY build/ input allowed.
    from local_pos.integrated_assets import load_assets
    load_assets(root)
    files.extend(p for p in (root / 'build/hybrid/integrated-ui/local_vendor').iterdir() if p.is_file())
    for src in sorted(set(files)):
        relative = src.relative_to(root)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        data = settings.encode('utf-8') if relative.as_posix() == 'NovaSoft/settings.py' else src.read_bytes()
        for secret in secret_values:
            if secret.encode() in data:
                # Never silently ship another copy of an embedded credential.
                raise RuntimeError('Una credencial incorporada permanece en ' + relative.as_posix() + '. Revisa antes de distribuir.')
        if src.suffix in {'.py', '.js', '.json', '.html', '.txt'}:
            if re.search(rb'(?:AIza[0-9A-Za-z_-]{30,}|gsk_[0-9A-Za-z]{25,}|gh[pousr]_[0-9A-Za-z]{25,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)', data):
                raise RuntimeError('Posible secreto en ' + relative.as_posix() + '; no se generó un paquete distribuible.')
        target.write_bytes(data)
    return secret_values


def extract(archive, target, prefix='', include=None):
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            if item.is_dir() or (prefix and not item.filename.startswith(prefix)):
                continue
            name = item.filename[len(prefix):]
            if '\\' in name or ':' in name or name.startswith('/') or Path(name).anchor or '..' in Path(name).parts:
                raise RuntimeError('Ruta inválida dentro de un componente.')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError('No se admiten enlaces dentro de componentes.')
            if include and not any(name.startswith(part) or name == part for part in include):
                continue
            path = Path(target) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as output, source.open(item) as stream:
                shutil.copyfileobj(stream, output)


def compile_launcher(output, *, payload=None, build=None):
    compiler = Path(os.environ['WINDIR']) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    command = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
               '/out:' + str(output), '/win32manifest:' + str(ROOT / 'windows_installer/app.manifest')]
    for assembly in ('System.Windows.Forms', 'System.Drawing', 'System.IO.Compression',
                     'System.IO.Compression.FileSystem', 'System.Web.Extensions', 'Microsoft.CSharp'):
        command.append('/reference:' + assembly + '.dll')
    if payload:
        definition = Path(build) / 'Payload.cs'
        definition.write_text('internal static class Payload { public const string Sha256="' + digest(payload) + '"; }', encoding='utf-8')
        command.extend(['/define:PACKAGE', '/resource:' + str(payload) + ',NovaPayload', str(definition)])
    command.append(str(ROOT / 'windows_installer/Launcher.cs'))
    subprocess.run(command, check=True)


def build(args):
    verified_component(args.python_zip)
    verified_component(args.postgres_zip)
    stage = Path(args.stage).resolve()
    if stage.exists():
        raise RuntimeError('Usa una carpeta NUEVA para compilar; no se borran ni sobreescriben datos.')
    stage.mkdir(parents=True)
    payload = stage / 'payload'
    secrets = source_stage(payload / 'app')
    extract(args.python_zip, payload / 'python')
    # Only server binaries, libraries, shared SQL definitions and upstream licenses.
    with zipfile.ZipFile(args.postgres_zip) as pg:
        prefixes = sorted({n.split('/')[0] for n in pg.namelist()})
    if prefixes != ['pgsql']:
        raise RuntimeError('La distribución PostgreSQL no tiene la estructura oficial esperada.')
    extract(args.postgres_zip, payload / 'postgresql', prefix='pgsql/',
            include=('bin/', 'lib/', 'share/', 'doc/', 'COPYRIGHT', 'LICENSE', 'license.txt'))
    requirements = stage / 'requirements-windows.txt'
    lines = (ROOT / 'requirements.txt').read_text().splitlines()
    # Installer uses maintained Django 5.2 LTS, not the obsolete 5.1 pin. The app source is identical.
    lines = ['Django>=5.2,<5.3' if re.match(r'^Django[=<>]', line) else line for line in lines]
    requirements.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    packages = payload / 'python/Lib/site-packages'
    # DAL 3.11 is distributed as a pure-Python sdist. Build its universal wheel
    # once on the build machine; never compile/download anything on a cashier PC.
    wheels = stage / 'wheels'
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', str(wheels),
                    'django-autocomplete-light==3.11.0'], check=True)
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-compile',
        '--only-binary=:all:', '--platform', 'win_amd64', '--python-version', '3.13',
        '--find-links', str(wheels), '--target', str(packages), '-r', str(requirements)], check=True)
    (payload / 'python/python313._pth').write_text('python313.zip\n.\nLib/site-packages\n../app\nimport site\n', encoding='ascii')
    compile_launcher(payload / 'NovaPOS.exe')
    shutil.copyfile(ROOT / 'docs/instalador_windows.md', payload / 'LEEME.md')
    shutil.copyfile(ROOT / 'docs/instalador_windows_respaldo_wsl2.md', payload / 'PRUEBAS-Y-LIMITACIONES.md')
    # Manifest hashes include every component and resolved distribution/version, no secret input.
    dependencies = []
    for metadata in packages.glob('*.dist-info/METADATA'):
        text = metadata.read_text(encoding='utf-8')
        dependencies.append({'name': re.search(r'^Name: (.+)$', text, re.M)[1],
                             'version': re.search(r'^Version: (.+)$', text, re.M)[1]})
    info = {'format': 'nova-windows-integrated-package-v1', 'python': '3.13', 'architecture': 'x64',
            'minimum_windows': '10', 'dependencies': sorted(dependencies, key=lambda row:row['name'].lower()),
            'components_sha256': {'python': digest(args.python_zip), 'postgresql': digest(args.postgres_zip)},
            'status': 'candidate_requires_clean_pc_and_signing_validation', 'files': {}}
    for path in sorted(payload.rglob('*')):
        if path.is_file():
            if path.suffix in {'.py', '.json', '.txt', '.js', '.html'} and any(secret.encode() in path.read_bytes() for secret in secrets):
                raise RuntimeError('Se detectó una credencial durante la verificación final.')
            info['files'][path.relative_to(payload).as_posix()] = digest(path)
    (payload / 'app/windows-package.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
    archive = stage / 'payload.zip'
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as target:
        for file in sorted(payload.rglob('*')):
            if file.is_file():
                target.write(file, file.relative_to(payload).as_posix())
    installer = stage / 'NovaPOS-Instalar-Windows-x64.exe'
    compile_launcher(installer, payload=archive, build=stage)
    (stage / 'SHA256.txt').write_text(digest(installer) + '  ' + installer.name + '\n', encoding='ascii')
    print('Instalador candidato generado:', installer)
    print('No firmado. No es una certificación de compatibilidad con Smart App Control.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', required=True)
    parser.add_argument('--python-zip', required=True, type=Path)
    parser.add_argument('--postgres-zip', required=True, type=Path)
    build(parser.parse_args())
