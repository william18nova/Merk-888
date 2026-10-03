"""Instalación por usuario, sin permisos de administrador ni credenciales de nube."""
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
from . import VERSION


def install():
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Instala usando un paquete compilado, no desde el código fuente.")
    source = Path(sys.executable).resolve().parent
    root = Path(os.environ["LOCALAPPDATA"]) / "NovaPOS" if os.name == "nt" else Path.home() / ".local/share/novapos-app"
    target = root / "versions" / VERSION
    executable = "NovaPOS.exe" if os.name == "nt" else "NovaPOS"
    if target.exists():
        raise RuntimeError("Esta versión ya está instalada. No se sobrescribió ni se borró ningún dato.")
    if not (source / "_internal/hybrid_client/assets/index.html").is_file():
        raise RuntimeError("Falta parte del paquete. Descomprime la carpeta completa antes de instalar.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.mkdir(mode=0o700)
    # Lista explícita: no copiar archivos ajenos que el usuario haya guardado
    # junto al ejecutable (ni posibles bases o credenciales).
    shutil.copy2(source / executable, target / executable)
    shutil.copytree(source / "_internal", target / "_internal")
    program = target / executable
    if os.name == "nt":
        # Las rutas se pasan en variables de entorno, nunca se interpolan en código.
        script = "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'),'Nova POS.lnk')); $s.TargetPath=$env:NOVA_INSTALL_EXE; $s.WorkingDirectory=$env:NOVA_INSTALL_DIR; $s.Save()"
        env = {**os.environ, "NOVA_INSTALL_EXE": str(program), "NOVA_INSTALL_DIR": str(target)}
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16le")).decode()], check=True, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        program.chmod(0o700)
        applications = Path.home() / ".local/share/applications"
        applications.mkdir(parents=True, exist_ok=True)
        quoted = str(program).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
        (applications / "novapos.desktop").write_text(f'[Desktop Entry]\nType=Application\nName=Nova POS\nExec="{quoted}"\nTerminal=true\nCategories=Office;\n', encoding="utf-8")
    return f"Instalado en {target}. Abre Nova POS desde el escritorio/menú de aplicaciones. Los datos se guardan por separado."
