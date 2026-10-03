"""Construir en el mismo SO de destino. Empaqueta solo módulos explícitos, nunca Django/settings."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-docs", action="store_true", help="Actualizar guías y hashes sin recompilar el ejecutable ya probado")
    args = parser.parse_args()
    if not args.refresh_docs:
        subprocess.run([sys.executable, str(ROOT / "scripts/build_hybrid_sale_assets.py")], check=True)
        subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--name", "NovaPOS",
            "--paths", str(ROOT), "--distpath", str(ROOT / "dist/hybrid"), "--workpath", str(ROOT / "build/hybrid"),
            "--specpath", str(ROOT / "build"), "--add-data", f"{ROOT / 'hybrid_client/assets'}:hybrid_client/assets",
            "--exclude-module", "django", "--exclude-module", "NovaSoft", "--exclude-module", "mainApp",
            str(ROOT / "hybrid_client/__main__.py"),
        ], check=True)
    folder = ROOT / "dist/hybrid/NovaPOS"
    if not (folder / ("NovaPOS.exe" if os.name == "nt" else "NovaPOS")).is_file():
        raise RuntimeError("Primero construye el paquete; no existe el ejecutable.")
    wrapper = "hybrid_install_windows.cmd" if os.name == "nt" else "hybrid_install_linux.sh"
    shutil.copy2(ROOT / "scripts" / wrapper, folder / ("Instalar.cmd" if os.name == "nt" else "instalar.sh"))
    shutil.copy2(ROOT / "docs/pos_hibrido_piloto.md", folder / "Guia-piloto.md")
    shutil.copy2(ROOT / "docs/pos_hibrido_aceptacion_sin_impresion.md", folder / "pos_hibrido_aceptacion_sin_impresion.md")
    shutil.copy2(ROOT / "docs/pos_hibrido_generar_venta_original.md", folder / "pos_hibrido_generar_venta_original.md")
    manifest = {str(p.relative_to(folder)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(folder.rglob("*")) if p.is_file() and p.name != "SHA256.json"}
    (folder / "SHA256.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Paquete creado: dist/hybrid/NovaPOS. Copia la carpeta completa; no solo el ejecutable.")
