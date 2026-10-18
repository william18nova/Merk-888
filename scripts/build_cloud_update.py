"""Paquete de cambios de servidor, sin credenciales, instaladores ni laboratorio."""
import hashlib
import json
from pathlib import Path
import subprocess
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)
def sha(data):
    return hashlib.sha256(data).hexdigest()

def build():
    names = set(git('diff', '--name-only', 'HEAD').decode().splitlines())
    names.update(git('ls-files', '--others', '--exclude-standard').decode().splitlines())
    names = {n for n in names if n.startswith(('mainApp/', 'local_pos/'))}
    names.update({'scripts/backup_configured_database.py', 'scripts/migrate_hybrid_with_backup.py',
                  'scripts/migrate_ptm_reader_with_backup.py', 'scripts/run_telegram_bot.sh'})
    prior = {}
    with ZipFile(ROOT / 'build/releases/NovaPOS-PTM-servidor-0.1.1.zip') as z:
        m = json.loads(z.read('manifest.json'))
        for n, entry in m['files'].items():
            data = z.read('payload/' + n)
            assert sha(data) == entry['sha256']
            prior[n] = sha(data)
    manifest = {'base': git('rev-parse', 'HEAD').decode().strip(), 'files': {}}
    target = ROOT / 'build/releases/NovaPOS-cloud-20261017.zip'
    with ZipFile(target, 'w', ZIP_DEFLATED) as z:
        for n in sorted(names):
            data = (ROOT / n).read_bytes()
            # Source normalization matches the reader's previously deployed package.
            if n.endswith(('.py', '.html', '.js', '.css', '.sh')):
                data = data.replace(b'\r\n', b'\n')
            try:
                before = sha(git('show', 'HEAD:' + n))
            except subprocess.CalledProcessError:
                before = None
            manifest['files'][n] = {'sha256': sha(data), 'before': prior.get(n, before)}
            z.writestr('payload/' + n, data)
        z.writestr('manifest.json', json.dumps(manifest, indent=2))
        z.writestr('deploy_cloud_update.py', (ROOT / 'scripts/deploy_cloud_update.py').read_bytes())
    print(target)
    print('Files:', len(names), 'SHA256:', sha(target.read_bytes()))

if __name__ == '__main__':
    build()
