"""Read-only PE inspection and optional disposable initdb reproduction."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import struct
import subprocess
from uuid import uuid4


def inspect(path):
    data = path.read_bytes()
    pe = struct.unpack_from('<I', data, 60)[0]
    if data[:2] != b'MZ' or data[pe:pe+4] != b'PE\0\0':
        raise ValueError('Invalid PE header')
    machine, count = struct.unpack_from('<HH', data, pe+4)
    size = struct.unpack_from('<H', data, pe+20)[0]
    optional = pe+24
    magic = struct.unpack_from('<H', data, optional)[0]
    directory = optional + (112 if magic == 0x20b else 96)
    sections = []
    for i in range(count):
        offset = optional+size+i*40
        virtual_size, address, raw_size, raw_offset = struct.unpack_from('<IIII', data, offset+8)
        sections.append((address, max(virtual_size, raw_size), raw_offset))
    def file_offset(rva):
        for address, length, raw in sections:
            if address <= rva < address+length:
                return raw+rva-address
        raise ValueError('Unmapped RVA')
    imports = []
    rva, length = struct.unpack_from('<II', data, directory+8)
    if rva:
        offset = file_offset(rva)
        for i in range(512):
            descriptor = struct.unpack_from('<IIIII', data, offset+i*20)
            if not any(descriptor):
                break
            start = file_offset(descriptor[3])
            imports.append(data[start:data.index(b'\0', start)].decode('ascii'))
    base = Path(r'C:\Program Files\PostgreSQL\18')
    search = [base/'bin', base/'lib', Path(os.environ['WINDIR'])/'System32']
    resolved = []
    for name in imports:
        candidate = next((folder/name for folder in search if (folder/name).is_file()), None)
        resolved.append({'name': name, 'path': str(candidate) if candidate else None,
                         'api_set': name.lower().startswith(('api-ms-', 'ext-ms-'))})
    return {'file': str(path), 'machine': hex(machine), 'pe_magic': hex(magic),
            'sha256': hashlib.sha256(data).hexdigest(), 'imports': resolved}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reproduce', action='store_true')
    args = parser.parse_args()
    base = Path(r'C:\Program Files\PostgreSQL\18')
    for path in (base/'lib/plpgsql.dll', base/'bin/postgres.exe'):
        print(json.dumps(inspect(path), indent=2))
    if args.reproduce:
        root = Path(os.environ['LOCALAPPDATA'])/'NovaPOS-TestTools'/('diagnostic-'+uuid4().hex)
        root.mkdir(parents=True)
        pw = root/'temporary-password.txt'
        pw.write_text(secrets.token_hex(32), encoding='ascii')
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('PG')}
        try:
            print('Reproduction started:', datetime.datetime.now().isoformat())
            result = subprocess.run([str(base/'bin/initdb.exe'), '-D', str(root/'data'), '-U',
                'diagnostic_only', '-A', 'scram-sha-256', '-E', 'UTF8', '--locale=C', '--pwfile='+str(pw)],
                env=env, capture_output=True, text=True, errors='replace', timeout=90)
            output = result.stdout+'\n'+result.stderr
            (root/'initdb.log').write_text(output, encoding='utf-8')
            print(output)
            print('Exit code:', result.returncode)
            print('Diagnostic log:', root/'initdb.log')
        finally:
            pw.unlink(missing_ok=True)
