"""Apply a narrowly scoped PTM reader package to an existing PythonAnywhere app.

Does not migrate, restart processes, issue credentials or import transactions.
Existing files must match the reviewed base; a private backup precedes writes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
from zipfile import ZipFile

EXISTING = {
    "mainApp/models.py", "mainApp/urls.py", "mainApp/permissions.py",
    "mainApp/templates/operaciones_ptm.html",
}
NEW = {
    "mainApp/ptm_reader_views.py", "mainApp/services/ptm_reader.py",
    "mainApp/migrations/0051_ptm_reader.py", "mainApp/templates/conciliacion_ptm.html",
    "mainApp/static/css/conciliacion_ptm.css", "mainApp/test_ptm_reader.py",
}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    temporary = path.with_name(path.name + ".ptm-" + uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def run(args):
    root = args.project.resolve(strict=True)
    if not (root / "manage.py").is_file():
        raise RuntimeError("No es el proyecto Django esperado.")
    with ZipFile(args.package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != "nova-ptm-deploy-v1" or set(manifest.get("files", {})) != EXISTING | NEW:
            raise RuntimeError("Paquete no reconocido.")
        payload = {name: archive.read("payload/" + name) for name in manifest["files"]}
    originals = {}
    for name, data in payload.items():
        entry = manifest["files"][name]
        if digest(data) != entry["sha256"]:
            raise RuntimeError("Contenido alterado: " + name)
        target = root / name
        if any(p.is_symlink() for p in (target, *target.parents)) or not target.resolve().is_relative_to(root):
            raise RuntimeError("Ruta inesperada: " + name)
        original = target.read_bytes() if target.is_file() else None
        if original == data:
            continue
        expected = entry.get("previous_sha256")
        if name in EXISTING and (original is None or digest(original) != expected):
            raise RuntimeError("Hay cambios no revisados en " + name + "; no se reemplazaron.")
        if name in NEW and original is not None and (not expected or digest(original) != expected):
            raise RuntimeError("Ya existe otro archivo en " + name + "; no se reemplazó.")
        if name in NEW and original is None and expected:
            raise RuntimeError("Falta el archivo de la versión anterior: " + name)
        originals[name] = original
    print("Archivos comprobados:", len(payload), "Cambios:", len(originals), flush=True)
    if not args.apply:
        print("Solo revisión. Usa --apply para respaldar y aplicar.")
        return
    if not originals:
        print("El paquete ya está aplicado. No se modificó nada.")
        return
    # Private code-only backup; no settings, tokens or database dumps in package.
    backup = Path.home() / "novapos-deploy-backups" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8])
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name, original in originals.items():
        if original is not None:
            saved = backup / name
            saved.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            saved.write_bytes(original)
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Respaldo:", backup, flush=True)
    written = []
    try:
        for name in originals:
            atomic_write(root / name, payload[name])
            written.append(name)
        env = {**os.environ, "DJANGO_SETTINGS_MODULE": "NovaSoft.settings"}
        subprocess.run([sys.executable, "manage.py", "check"], cwd=root, env=env, check=True, timeout=90)
        subprocess.run([sys.executable, "-m", "django", "test", "mainApp.test_ptm_reader", "mainApp.test_ptm",
                        "--settings=NovaSoft.test_settings", "--noinput"], cwd=root, env=env, check=True, timeout=120)
    except BaseException:
        for name in reversed(written):
            if originals[name] is None:
                (root / name).unlink()
            else:
                atomic_write(root / name, originals[name])
        raise
    (backup / "complete.json").write_text(json.dumps({"complete": True, "files": written}), encoding="utf-8")
    print("OK: código y pruebas verificados. Falta collectstatic y Reload; no se tocaron datos ni procesos.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--apply", action="store_true")
    run(parser.parse_args())
