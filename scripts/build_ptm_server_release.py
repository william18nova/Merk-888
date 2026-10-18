"""Package only the PTM reader, excluding all other uncommitted changes."""
import ast
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.deploy_ptm_reader import EXISTING, NEW


def build(previous_package=None, output=None):
    if "class BolsaEfectivoPTM" in (ROOT / "mainApp/models.py").read_text(encoding="utf-8"):
        raise RuntimeError("El proyecto incluye bolsas PTM y otra migración. Este empaquetador solo despliega el lector 0051; usa el despliegue completo con respaldo y migraciones.")
    reviewed = {
        "mainApp/models.py": "554f7af7c809d7b424c01539a1881b53dc181788b927af8158307c32668e7447",
        "mainApp/urls.py": "f76d93311bae21992eeaa5135eca0e8fd99a14ceb09c71c021c387028e0102b4",
        "mainApp/permissions.py": "af3e3443ee40e2b657503418b3d7419ff37bae8b3e1ca8acda573c0f67bd9d8c",
        "mainApp/templates/operaciones_ptm.html": "d1600f0c422c0a8171af6c7f802748c55070c3bb0653f6733e20fe3b1845f75a",
    }
    payload = {}
    manifest = {"format": "nova-ptm-deploy-v1", "files": {}}
    previous = {}
    if previous_package:
        with ZipFile(previous_package) as archive:
            prior_manifest = json.loads(archive.read("manifest.json"))
            if prior_manifest.get("format") != manifest["format"] or set(prior_manifest["files"]) != EXISTING | NEW:
                raise RuntimeError("Paquete anterior no reconocido.")
            for name, entry in prior_manifest["files"].items():
                prior_data = archive.read("payload/" + name)
                if hashlib.sha256(prior_data).hexdigest() != entry["sha256"]:
                    raise RuntimeError("El paquete anterior fue alterado: " + name)
                previous[name] = entry["sha256"]
    for name in sorted(EXISTING | NEW):
        text = (ROOT / name).read_text(encoding="utf-8")
        if name == "mainApp/urls.py":
            text = text.replace("from .connection_status import database_status\n", "")
            text = text.replace('    path("api/estado-conexion/", database_status, name="database_connection_status"),\n', "")
        if name == "mainApp/permissions.py":
            text = text.replace(', "database_connection_status"', "")
        data = text.encode("utf-8")
        if name.endswith(".py"):
            ast.parse(text, filename=name)
        entry = {"sha256": hashlib.sha256(data).hexdigest()}
        if name in EXISTING:
            base = subprocess.run(["git", "show", "HEAD:" + name], cwd=ROOT, check=True, capture_output=True).stdout
            if hashlib.sha256(base).hexdigest() != reviewed[name]:
                raise RuntimeError("La base revisada no coincide: " + name)
            entry["previous_sha256"] = reviewed[name]
        if previous:
            entry["previous_sha256"] = previous[name]
        payload[name] = data
        manifest["files"][name] = entry
    target = output or ROOT / "build/releases/NovaPOS-PTM-servidor-0.1.0.zip"
    if previous_package and target.resolve() == previous_package.resolve():
        raise RuntimeError("El paquete anterior debe conservarse.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        archive.writestr("deploy_ptm_reader.py", (ROOT / "scripts/deploy_ptm_reader.py").read_bytes())
        for name, data in payload.items():
            archive.writestr("payload/" + name, data)
    print(target)
    print("SHA256", hashlib.sha256(target.read_bytes()).hexdigest())
    print("Files", len(payload))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-package", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    build(args.previous_package, args.output)
