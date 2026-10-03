"""Distribución fuente privada; excluye settings productivos, datos y secretos."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def build(destination):
    destination = Path(destination).absolute()
    if destination.exists():
        raise RuntimeError("El destino debe ser nuevo; no se sobrescribe una distribución.")
    # Lista positiva: nunca copiar el repositorio completo ni datos del usuario.
    files = []
    for directory in ("mainApp", "local_pos", "pos_shared", "hybrid_client"):
        for source in (ROOT/directory).rglob("*"):
            if not source.is_file() or source.is_symlink() or any(part.startswith(".") or part == "__pycache__" for part in source.relative_to(ROOT).parts):
                continue
            if source.name.startswith("test") or source.name in {"settings.py", "local_settings.py"}:
                continue
            if source.suffix.lower() in {".py", ".json", ".html", ".css", ".js", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2", ".ttf", ".eot"}:
                files.append(source)
    for relative in ("NovaSoft/__init__.py", "NovaSoft/urls.py", "NovaSoft/hybrid_local_settings.py",
                     "NovaSoft/hybrid_installed_settings.py", "requirements.txt", "requirements_full_local.txt",
                     "requirements_pilot.txt", "scripts/pilot_launcher.py",
                     "docs/pos_hibrido_dos_computadores.md",
                     "docs/pos_hibrido_operaciones_instalacion.md",
                     "docs/pos_hibrido_actualizaciones.md",
                     "docs/pos_hibrido_revision.md",
                     "scripts/install_full_local.ps1", "scripts/install_full_local.sh",
                     "scripts/switch_full_local.ps1", "scripts/switch_full_local.sh",
                     "scripts/update_full_local.ps1", "scripts/update_full_local.sh",
                     "scripts/start_full_local.ps1", "scripts/start_full_local.sh"):
        files.append(ROOT/relative)
    secret_pattern = re.compile(rb"(?:AIza[0-9A-Za-z_-]{25,}|gsk_[0-9A-Za-z]{30,}|[0-9]{8,12}:AA[0-9A-Za-z_-]{25,})")
    for source in files:
        if source.suffix in {".py", ".json", ".js", ".html"} and secret_pattern.search(source.read_bytes()):
            raise RuntimeError("Posible credencial en un archivo de distribución: " + str(source.relative_to(ROOT)))
    destination.mkdir(parents=True)
    for source in files:
        target = destination/source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for name in ("PRUEBAS.cmd", "PRUEBAS.sh"):
        shutil.copyfile(ROOT / "scripts" / name, destination / name)
    shutil.copyfile(ROOT / "docs/pos_hibrido_dos_computadores.md", destination / "LEEME-PRIMERO.md")
    from scripts.build_full_local_assets import build as assets
    assets(destination)
    (destination/"local_vendor").rename(destination/"offline_vendor")
    hashes = {p.relative_to(destination).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(destination.rglob("*")) if p.is_file()}
    package_id = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    manifest = {"format": "nova-full-local-package-v1", "package_id": package_id, "files": hashes,
                "signed": False, "production_certified": False}
    from local_pos.schema_versions import schema_version
    manifest["schema_version"] = schema_version(manifest)
    (destination/"full-local-package.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Distribución candidata creada: {destination}. Sin firma de código; requiere validación antes de producción.")


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    build(parser.parse_args().destination)
