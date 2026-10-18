"""Valida base exacta y respalda código antes de aplicar un paquete revisado."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

def sha(data):
    return hashlib.sha256(data).hexdigest()

def run(args):
    root = args.project.resolve(strict=True)
    with ZipFile(args.package) as z:
        manifest = json.loads(z.read('manifest.json'))
        payload = {n: z.read('payload/' + n) for n in manifest['files']}
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip()
    if head != manifest['base']:
        raise RuntimeError('La base Git del servidor no coincide')
    originals = {}
    for n, data in payload.items():
        p = root / n
        if (not n.startswith(('mainApp/', 'local_pos/', 'scripts/')) or '..' in Path(n).parts
                or any(x.is_symlink() for x in (p, *p.parents))):
            raise RuntimeError('Ruta no permitida: ' + n)
        entry = manifest['files'][n]
        if sha(data) != entry['sha256']:
            raise RuntimeError('Paquete alterado: ' + n)
        old = p.read_bytes() if p.exists() else None
        if old == data:
            continue
        if (sha(old) if old is not None else None) != entry['before']:
            raise RuntimeError('Cambio no revisado en servidor: ' + n)
        originals[n] = old
    print('Comparación correcta. Archivos a actualizar:', len(originals), flush=True)
    if not args.apply:
        return
    if not args.backup_manifest:
        raise RuntimeError('Falta el respaldo de base de datos')
    sys.path.insert(0, str(root))
    os.environ['DJANGO_SETTINGS_MODULE'] = 'NovaSoft.settings'
    import django
    django.setup()
    from django.conf import settings
    from scripts.migrate_hybrid_with_backup import verified_backup
    verified_backup(args.backup_manifest, settings.DATABASES['default'])
    backup = Path.home() / 'novapos-deploy-backups' / datetime.now(timezone.utc).strftime('cloud-%Y%m%dT%H%M%S')
    backup.mkdir(parents=True, mode=0o700, exist_ok=False)
    (backup / 'files.json').write_text(json.dumps({n: old is not None for n, old in originals.items()}))
    for n, old in originals.items():
        if old is not None:
            p = backup / n
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(old)
    written = []
    try:
        for n in originals:
            p = root / n
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + '.cloud-tmp')
            tmp.write_bytes(payload[n])
            os.replace(tmp, p)
            written.append(n)
        subprocess.run([sys.executable, 'manage.py', 'check'], cwd=root, check=True)
        subprocess.run([sys.executable, '-m', 'django', 'test', 'mainApp.test_ptm_cash_pool',
            'mainApp.test_ptm_reader', 'mainApp.test_ptm', 'mainApp.test_permission_audit',
            'mainApp.test_connection_status', 'mainApp.test_telegram_runtime',
            '--settings=NovaSoft.test_settings', '--noinput'], cwd=root, check=True)
    except BaseException:
        for n in reversed(written):
            if originals[n] is None:
                (root / n).unlink()
            else:
                (root / n).write_bytes(originals[n])
        raise
    print('Código verificado. Respaldo:', backup)
    print('Faltan migración 0052, collectstatic y reload. No se activó ninguna bolsa.')

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('package', type=Path)
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--backup-manifest', type=Path)
    p.add_argument('--apply', action='store_true')
    run(p.parse_args())
