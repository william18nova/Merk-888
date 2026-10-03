"""Archives only manifest-listed files, never a venv, database, key or lab identity."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tarfile
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_pos.runtime import package_manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    package=args.package.resolve()
    manifest=package_manifest(package)
    if args.output.exists(): raise RuntimeError('El destino debe ser nuevo; no se sobrescriben entregas.')
    args.output.mkdir(parents=True)
    names=sorted([*manifest['files'],'full-local-package.json'])
    prefix='NovaPOS-Pruebas'
    result={'package_id':manifest['package_id'],'schema_version':manifest['schema_version'],'files':len(names),'archives':[]}
    for system,extension in (('Windows','zip'),('Linux','tar.gz')):
        target=args.output/f'NovaPOS-Piloto-{system}.{extension}'
        if extension=='zip':
            with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
                for name in names: archive.write(package/name,f'{prefix}/{name}')
            with zipfile.ZipFile(target) as archive:
                assert archive.testzip() is None
                assert len(archive.namelist())==len(names)
        else:
            with tarfile.open(target,'x:gz') as archive:
                for name in names:
                    info=archive.gettarinfo(str(package/name),arcname=f'{prefix}/{name}')
                    info.uid=info.gid=0; info.uname=info.gname=''; info.mode=0o755 if name.endswith('.sh') else 0o644
                    with (package/name).open('rb') as stream: archive.addfile(info,stream)
            with tarfile.open(target,'r:gz') as archive:
                assert len(archive.getmembers())==len(names)
        result['archives'].append({'file':target.name,'bytes':target.stat().st_size,
                                  'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
    (args.output/'SHA256SUMS.txt').write_text(''.join(f"{row['sha256']}  {row['file']}\n" for row in result['archives']),encoding='ascii')
    (args.output/'paquete.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__': main()
