"""Asistente multiplataforma; no cambia firewall, antivirus ni PostgreSQL global."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

PACKAGE = Path(__file__).resolve().parents[1]


def run(command):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG") and k not in
           {"DATABASE_URL", "DJANGO_SETTINGS_MODULE", "NOVA_LOCAL_CONFIG", "PYTHONPATH", "SSL_CERT_FILE"}}
    env["PYTHONUTF8"] = "1"
    result = subprocess.run(list(map(str, command)), cwd=PACKAGE, env=env)
    if result.returncode:
        print("No se completó el paso. No borres datos ni vuelvas a inicializar una caja que ya utilizaste.")
    return result.returncode


def dependencies():
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        raise RuntimeError("Este piloto está preparado para Python 3.10–3.12. En Windows usa Python 3.12 de 64 bits.")
    if os.name == "posix" and os.geteuid() == 0:
        raise RuntimeError("Ejecuta como usuario normal, no con sudo/root.")
    python = PACKAGE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = PACKAGE / ".venv/pilot-ready.txt"
    if not python.exists() and run([sys.executable, "-m", "venv", PACKAGE / ".venv"]):
        raise RuntimeError("No se creó el entorno Python; en Linux verifica que python3-venv esté instalado.")
    if not marker.exists():
        print("Preparando dependencias privadas (solo la primera vez requiere internet).")
        args = [python, "-m", "pip", "install", "-r", "requirements_pilot.txt"]
        if (PACKAGE / "wheelhouse").is_dir():
            args += ["--no-index", "--find-links", str(PACKAGE / "wheelhouse")]
        if run(args) or run([python, "-m", "pip", "check"]):
            raise RuntimeError("Dependencias incompletas. Vuelve a abrir el asistente con conexión.")
        # Do not trust a successful download alone: validate immutable source.
        if run([python, "-c", "from local_pos.runtime import package_manifest; package_manifest(); print('Paquete verificado')"]):
            raise RuntimeError("El paquete no supera la verificación.")
        marker.write_text("ready", encoding="ascii")
    return python


def pg_directory():
    roots = (Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PostgreSQL",) if os.name == "nt" else (Path("/usr/lib/postgresql"),)
    candidates = [path / "bin" for root in roots if root.is_dir() for path in root.iterdir()
                  if path.is_dir() and (path / "bin" / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")).is_file()]
    candidates.sort(key=lambda p: tuple(int(x) for x in p.parent.name.split(".") if x.isdigit()), reverse=True)
    default = str(candidates[0]) if candidates else ""
    return Path(input(f"Carpeta bin de PostgreSQL [{default}]: ").strip().strip('"') or default).absolute()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Solo comprobar instalador y dependencias")
    args = parser.parse_args()
    python = dependencies()
    if args.check:
        return run([python, "-c", "from local_pos.runtime import package_manifest; print(package_manifest()['package_id'])"])
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "NovaPOS-Pruebas" if os.name == "nt" else Path.home() / ".local/share/nova-pos-pruebas"
    client, hub = base / "caja", base / "servidor"
    while True:
        print(f"\nNOVA POS — PRUEBAS FICTICIAS / NO USAR CON DINERO REAL\nDatos locales: {base}\n"
              "1. Preparar la caja de ESTE computador (solo una vez)\n"
              "2. Crear servidor ficticio (SOLO en el computador 1)\n"
              "3. Iniciar servidor ficticio (dejar esta ventana abierta)\n"
              "4. Vincular esta caja al servidor de pruebas\n"
              "5. Abrir la caja / reanudarla después de reiniciar\n"
              "6. Ver resumen del servidor (ventas, inventario, cierres)\n"
              "7. Respaldar esta caja (primero detén la opción 5 con Ctrl+C)\n"
              "0. Salir")
        choice = input("Opción: ").strip()
        runtime_args = [python, "-B", "-m", "local_pos.runtime", "--data-dir", client]
        pilot_args = [python, "-B", "-m", "local_pos.pilot", "--data-dir", hub]
        if choice == "0":
            return 0
        if choice == "1":
            print("El usuario LOCAL será laboratorio. Elige una contraseña nueva de al menos 12 caracteres.")
            run([*runtime_args, "init", "--pg-bin", pg_directory(), "--username", "laboratorio"])
        elif choice == "2":
            host = input("IPv4 privada del computador 1 (ejemplo 192.168.1.20, NO la del router): ").strip()
            run([*pilot_args, "hub-init", "--pg-bin", pg_directory(), "--host", host])
        elif choice == "3":
            run([*pilot_args, "hub-serve"])
        elif choice == "4":
            link = input("Ruta del archivo conexion-pruebas.json copiado desde el computador 1: ").strip().strip('"')
            username = input("Usuario del servidor (prueba1 para PC1, prueba2 para PC2): ").strip()
            run([python, "-B", "-m", "local_pos.pilot", "--data-dir", client, "connect", "--link", link, "--username", username])
        elif choice == "5":
            print("Cuando indique POS local, abre http://127.0.0.1:8910/ — usuario laboratorio y tu contraseña LOCAL.")
            print("Deja la ventana abierta. Ctrl+C detiene el POS de forma segura, no borra la base.")
            run([*runtime_args, "serve"])
        elif choice == "6":
            run([*pilot_args, "hub-summary"])
        elif choice == "7":
            from datetime import datetime
            run([*runtime_args, "backup", "--output", base / "respaldos" / (datetime.now().strftime("%Y%m%d-%H%M%S") + ".zip")])
        else:
            print("Elige una opción de la lista.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nAsistente detenido. Conserva las carpetas de datos.")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
