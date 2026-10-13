"""Prepare a new private engine for the NORMAL manage.py runserver.

No cloud credentials, production writes, system services, firewall changes or
Windows security exclusions. WSL is an explicit development-only choice.
"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(args):
    from local_pos.integrated_config import config_path, register_location
    from local_pos.integrated_prepare import prepare
    path = config_path(ROOT)
    if path.exists() or (ROOT / ".novapos-runtime.json").exists():
        raise RuntimeError("Ya existe una instalación o referencia. No se reemplazaron bases ni credenciales.")
    if bool(args.wsl_distribution) != bool(args.wsl_user):
        raise RuntimeError("Indica distribución y usuario WSL juntos.")
    from scripts.prepare_integrated_assets import prepare_assets
    prepare_assets()
    prepare(ROOT, path, args.pg_bin, port=args.port,
        wsl_distribution=args.wsl_distribution, wsl_user=args.wsl_user,
        integrated_preview=not args.wsl_distribution)
    # Preparation alone remains database_only on every OS, so the habitual app
    # still uses cloud authentication until the explicit authorized activation.
    from local_pos.runtime import read_json, save_json
    config = read_json(path)
    config["activation"] = "database_only"
    save_json(path, config)
    register_location(path, ROOT)
    print("Motor privado preparado y detenido. No se cambió la base de la nube.")
    print("La activación requiere el usuario, caja y turno propios de ESTE equipo.")
    if args.wsl_distribution:
        print("WSL seleccionado explícitamente: configuración de desarrollo, no recomendación automática para clientes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", required=True)
    parser.add_argument("--port", type=int, default=55460)
    parser.add_argument("--wsl-distribution")
    parser.add_argument("--wsl-user")
    try:
        run(parser.parse_args())
    except Exception as exc:
        print(str(exc) if isinstance(exc, RuntimeError) else
              f"Preparación interrumpida ({type(exc).__name__}); los archivos se conservaron. No desactives las protecciones.", file=sys.stderr)
        sys.exit(1)
