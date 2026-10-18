"""Executed as root ONLY inside the installer's freshly imported, owned distro."""
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys


def main():
    name = sys.argv[1]
    if sys.platform != 'linux' or os.geteuid() != 0 or not re.fullmatch(r'NovaPOS-[0-9a-f]{16}', name):
        raise RuntimeError('This provisioner requires the dedicated NovaPOS WSL distribution.')
    if os.environ.get('WSL_DISTRO_NAME') != name:
        raise RuntimeError('WSL distribution identity mismatch.')
    marker = Path('/etc/novapos-installer.json')
    identity = dict(name=name, format='nova-owned-wsl-linux-v1')
    if marker.exists():
        if json.loads(marker.read_text()) != identity:
            raise RuntimeError('Foreign distribution marker.')
    else:
        if list(Path('/var/lib/postgresql').glob('*/main/PG_VERSION')):
            raise RuntimeError('Existing PostgreSQL cluster; refusing changes.')
        with marker.open('x') as stream:
            json.dump(identity, stream)
    env = dict(os.environ, DEBIAN_FRONTEND='noninteractive', LC_ALL='C.UTF-8')
    # Only our imported distribution: no default SQL service or public 5432.
    policy = Path('/usr/sbin/policy-rc.d')
    original_policy = policy.read_bytes() if policy.exists() else None
    original_mode = policy.stat().st_mode & 0o777 if policy.exists() else 0o755
    policy.write_text('#!/bin/sh\nexit 101\n')
    policy.chmod(0o755)
    try:
        subprocess.run(['apt-get', 'update'], env=env, check=True, timeout=300)
        subprocess.run(['apt-get', '-y', 'upgrade'], env=env, check=True, timeout=900)
        subprocess.run(['apt-get', '-y', 'install', 'postgresql-common', 'ca-certificates'], env=env, check=True, timeout=300)
        Path('/etc/postgresql-common/createcluster.conf').write_text('create_main_cluster = false\n')
        subprocess.run(['apt-get', '-y', 'install', 'postgresql-16', 'postgresql-client-16'], env=env, check=True, timeout=600)
    finally:
        if original_policy is None:
            policy.unlink(missing_ok=True)
        else:
            policy.write_bytes(original_policy)
            policy.chmod(original_mode)
    try:
        user = pwd.getpwnam('novapos')
    except KeyError:
        subprocess.run(['useradd', '--create-home', '--shell', '/bin/bash', 'novapos'], check=True)
        user = pwd.getpwnam('novapos')
    if user.pw_uid == 0 or user.pw_dir != '/home/novapos':
        raise RuntimeError('Unsafe database user.')
    # No sudo grant, password or shell login needed. Windows addresses this user
    # explicitly. Leave global .wslconfig and all other distributions untouched.
    Path('/home/novapos').chmod(0o700)
    subprocess.run(['/usr/lib/postgresql/16/bin/postgres', '--version'], check=True)
    print('Dedicated NovaPOS WSL2 components ready; no business database created.')


if __name__ == '__main__':
    main()
