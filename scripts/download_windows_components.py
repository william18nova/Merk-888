"""Explicit public official downloads; never executes installers or modifies OS."""
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
import requests

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    'python-3.13.16-embed-amd64.zip': 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip',
    # Resolved from EDB's official download page (fileid=1260623).
    'postgresql-16.15-windows-x64.zip': 'https://get.enterprisedb.com/postgresql/postgresql-16.15-5-windows-x64-binaries.zip',
}
HOSTS = {'www.python.org', 'sbp.enterprisedb.com', 'get.enterprisedb.com'}


def main():
    target = ROOT / 'build/windows-integrated-downloads'
    target.mkdir(parents=True, exist_ok=True)
    lock = target / 'components.json'
    manifest = json.loads(lock.read_text()) if lock.exists() else {}
    for name, url in COMPONENTS.items():
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.hostname not in HOSTS:
            raise RuntimeError('Componente fuera de los proveedores HTTPS permitidos.')
        path = target / name
        if path.exists():
            with path.open('rb') as stream:
                actual = hashlib.file_digest(stream, 'sha256').hexdigest()
            if name not in manifest or actual != manifest[name]['sha256']:
                raise RuntimeError('Descarga existente sin verificar; no se reemplazó.')
            print('Caché verificada:', name, flush=True)
            continue
        digest = hashlib.sha256()
        print('Descargando del proveedor oficial:', name, flush=True)
        with requests.get(url, stream=True, timeout=(15, 45), allow_redirects=False) as response, path.with_suffix('.partial').open('xb') as output:
            if response.status_code != 200:
                raise RuntimeError('El proveedor no devolvió el componente directamente; revisa su enlace oficial.')
            total = 0
            for block in response.iter_content(1024 * 1024):
                total += len(block)
                if total > 600_000_000:
                    raise RuntimeError('Componente demasiado grande.')
                digest.update(block)
                output.write(block)
                if total % (32 * 1024 * 1024) == 0:
                    print('Descargado:', total // (1024 * 1024), 'MB', flush=True)
            final_url = response.url
        path.with_suffix('.partial').replace(path)
        manifest[name] = {'source': url, 'resolved_url': final_url, 'sha256': digest.hexdigest(), 'size': total}
        lock.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        print('Descarga finalizada:', name, total, 'bytes', flush=True)


if __name__ == '__main__':
    main()
