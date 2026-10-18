"""Provision only a dedicated, verified Ubuntu WSL2 distribution for a NEW POS."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from uuid import uuid4

from local_pos.runtime import private_dir, save_json
from windows_installer.engines import state_root

ROOTFS_URL = 'https://cdimage.ubuntu.com/ubuntu-base/releases/24.04/release/ubuntu-base-24.04.5-base-amd64.tar.gz'
# Canonical SHA256SUMS, pinned: an upstream change must be reviewed, never silently accepted.
ROOTFS_SHA256 = 'e77b6f10c2590cef872b33ee9f635a0e3fd1f57fb074c0e52b5c7f56147a0c86'


class WslUnavailable(RuntimeError):
    pass


def wsl_executable():
    return str(Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/wsl.exe')


def decode_output(data):
    return data.decode('utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else
                       'utf-16-le' if b'\x00' in data else 'utf-8', errors='replace').strip()


def distribution_name(project):
    key = hashlib.sha256(os.path.normcase(str(Path(project).resolve())).encode()).hexdigest()[:16]
    return 'NovaPOS-' + key


def call(*args, timeout=90, input=None):
    return subprocess.run([wsl_executable(), *map(str, args)], input=input, capture_output=True,
                          timeout=timeout, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def registered_location(name):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Lxss') as parent:
            for index in range(winreg.QueryInfoKey(parent)[0]):
                with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as child:
                    if winreg.QueryValueEx(child, 'DistributionName')[0] == name:
                        return winreg.QueryValueEx(child, 'BasePath')[0], winreg.QueryValueEx(child, 'Version')[0]
    except FileNotFoundError:
        return None
    return None


def ensure_owned(name, destination):
    found = registered_location(name)
    if not found:
        raise RuntimeError('No se confirmó la importación WSL. No se tocó otra distribución.')
    base, version = found
    # Registry may use the extended-length Windows path prefix.
    base = base.removeprefix('\\\\?\\')
    if Path(base).resolve() != Path(destination).resolve() or version != 2:
        raise RuntimeError('Ese nombre WSL pertenece a otra instalación o no usa WSL2. No se modificó.')


def download(root, report):
    import requests
    target = root / 'ubuntu-24.04-amd64.tar.gz'
    if target.exists():
        with target.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != ROOTFS_SHA256:
                raise RuntimeError('La imagen WSL no coincide con Canonical. Se conservó sin instalar.')
        return target
    partial = root / ('ubuntu-download-' + uuid4().hex + '.partial')
    report('Descargando Ubuntu Base desde Canonical (aproximadamente 29 MB); requiere internet y espacio adicional…')
    digest = hashlib.sha256()
    count = 0
    next_progress = 4 * 1024**2
    deadline = time.monotonic() + 900
    with requests.get(ROOTFS_URL, stream=True, timeout=(15, 60), allow_redirects=False) as response:
        if response.status_code != 200:
            raise RuntimeError('Canonical no entregó la imagen oficial. Reintenta sin cambiar las protecciones.')
        with partial.open('xb') as stream:
            for block in response.iter_content(64 * 1024):
                if time.monotonic() > deadline:
                    raise RuntimeError('La descarga está tardando demasiado. Se conservó el diagnóstico; reintenta con una conexión estable.')
                count += len(block)
                if count > 100_000_000:
                    raise RuntimeError('La imagen descargada excede el tamaño previsto.')
                digest.update(block)
                stream.write(block)
                if count >= next_progress:
                    report('Descargando Ubuntu: ' + str(count // (1024**2)) + ' MB recibidos…')
                    next_progress += 4 * 1024**2
    if digest.hexdigest() != ROOTFS_SHA256:
        raise RuntimeError('Falló la verificación SHA-256 de Ubuntu. No se importó la imagen.')
    partial.rename(target)
    return target


def provision(project, report):
    if os.name != 'nt' or sys.getwindowsversion().build < 19041:
        raise RuntimeError('El respaldo automático WSL2 necesita Windows 10 compilación 19041 o posterior, x64 y virtualización habilitada.')
    if not Path(wsl_executable()).is_file():
        raise WslUnavailable('WSL no está disponible. Instálalo con autorización de administrador y vuelve a abrir NovaPOS después del reinicio si Windows lo solicita.')
    status = call('--status')
    if status.returncode:
        raise WslUnavailable('WSL no está listo. Puede faltar instalación, virtualización o reiniciar Windows. El asistente puede solicitar instalar WSL; no reinicia ni cambia protecciones.')
    root = private_dir(state_root(project) / 'wsl')
    name, destination = distribution_name(project), root / 'distribution'
    marker = root / 'ownership.json'
    identity = dict(format='nova-owned-wsl-v1', name=name, project=str(Path(project).resolve()))
    found = registered_location(name)
    if marker.exists():
        if json.loads(marker.read_text(encoding='utf-8')) != identity:
            raise RuntimeError('La distribución WSL registrada pertenece a otro proyecto.')
    else:
        if found or destination.exists():
            raise RuntimeError('Ya existe una distribución o carpeta con ese nombre. No se reutilizará ni borrará.')
        save_json(marker, identity)
    if not found:
        if destination.exists():
            raise RuntimeError('Hay una importación WSL incompleta. Se conservaron sus archivos; revisa el diagnóstico antes de continuar.')
        if shutil.disk_usage(root).free < 5 * 1024**3:
            raise RuntimeError('Se necesitan al menos 5 GB libres para preparar el respaldo WSL2.')
        archive = download(root, report)
        report('Importando una distribución WSL2 exclusiva de NovaPOS. Las otras distribuciones no se modifican…')
        result = call('--import', name, destination, archive, '--version', '2', timeout=600)
        if result.returncode:
            (root / 'import-error.txt').write_text(decode_output(result.stdout + result.stderr), encoding='utf-8')
            raise RuntimeError('No se pudo importar WSL2. Revisa virtualización/reinicio y el diagnóstico ' + str(root / 'import-error.txt') + '. No se borró ninguna distribución.')
    ensure_owned(name, destination)
    # The official minimal rootfs contains apt but not Python. Bootstrap only
    # from its authenticated Ubuntu repositories, inside our owned distribution.
    for arguments in (('update',), ('-y', '--no-install-recommends', 'install', 'python3', 'ca-certificates')):
        report('Preparando los componentes mínimos del Ubuntu exclusivo…')
        result = call('--distribution', name, '--user', 'root', '--exec', '/usr/bin/env',
                      'DEBIAN_FRONTEND=noninteractive', 'apt-get', *arguments, timeout=600)
        with (root / 'bootstrap.log').open('a', encoding='utf-8') as log:
            log.write(decode_output(result.stdout + result.stderr) + '\n')
        if result.returncode:
            raise RuntimeError('No se pudo preparar Python en el Ubuntu exclusivo; reintenta. Diagnóstico: ' + str(root / 'bootstrap.log'))
    script = Path(__file__).with_name('wsl_provision.py').read_bytes()
    report('Instalando PostgreSQL 16 y actualizaciones de seguridad dentro del Ubuntu exclusivo…')
    result = call('--distribution', name, '--user', 'root', '--exec', 'python3', '-B', '-', name,
                  input=script, timeout=1800)
    (root / 'provision.log').write_text(decode_output(result.stdout + result.stderr), encoding='utf-8')
    if result.returncode:
        raise RuntimeError('La preparación Linux no terminó. Se conservó para reintentar; consulta ' + str(root / 'provision.log'))
    return name
