"""Private configuration for the normal runserver; no cloud credentials here."""
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID

from .runtime import read_json

FORMAT = "nova-integrated-runserver-v1"
DATABASE = "nova_integrated_local"


def default_path(project):
    project = str(Path(project).resolve())
    key = hashlib.sha256(os.path.normcase(project).encode()).hexdigest()[:16]
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local/share"))
    return base / "NovaPOS" / "integrated" / key / "config.json"


def config_path(project):
    configured = os.environ.get("NOVA_INTEGRATED_CONFIG")
    if configured:
        return Path(configured)
    return _registered_path(project) or default_path(project)


def _registered_path(project):
    # An operator may prepare from a packaged Windows process whose AppData is
    # redirected. Pin the verified physical path so an ordinary terminal finds
    # the SAME installation/lock, not an empty second database. No secrets here.
    locator = Path(project) / ".novapos-runtime.json"
    if locator.exists() or locator.is_symlink():
        if (locator.is_symlink() or not locator.is_file() or locator.stat().st_size > 4096):
            raise RuntimeError("La referencia local de instalación no es válida.")
        try:
            value = json.loads(locator.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise RuntimeError("No se pudo leer la referencia local de instalación.") from None
        if (not isinstance(value, dict) or set(value) != {"format", "config"}
                or value["format"] != "nova-integrated-location-v1"
                or not isinstance(value["config"], str) or not Path(value["config"]).is_absolute()):
            raise RuntimeError("La referencia local de instalación es incompleta.")
        return private_config_path(value["config"], project)
    return None


def register_location(path, project):
    """Machine-local pointer only; never copy it to another PC or commit it."""
    from .runtime import save_json
    path = private_config_path(path, project)
    load_config(path, project)
    locator = Path(project) / ".novapos-runtime.json"
    previous = _registered_path(project)
    if previous is not None and previous != path:
        raise RuntimeError("El proyecto ya referencia otra instalación; no se reemplazó.")
    save_json(locator, {"format": "nova-integrated-location-v1", "config": str(path)})


def private_config_path(path, project):
    path, project = Path(path).absolute(), Path(project).resolve()
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)():
            raise RuntimeError("La configuración no admite enlaces ni uniones de carpetas.")
    path = path.resolve()
    if path.is_relative_to(project):
        raise RuntimeError("La configuración privada debe estar fuera del repositorio.")
    return path


def load_config(path, project, *, expected_schema=None):
    path, project = private_config_path(path, project), Path(project).resolve()
    data = read_json(path)
    keys = {"format", "phase", "project", "data_dir", "instance_id", "pg_bin", "port",
            "database", "user", "password", "schema_signature", "secret_key"}
    if isinstance(data, dict) and "backend" in data:
        keys.add("backend")
    if isinstance(data, dict) and "activation" in data:
        keys.add("activation")
    if not isinstance(data, dict) or set(data) != keys:
        raise RuntimeError("Configuración integrada incompleta o desconocida.")
    if data["format"] != FORMAT or data["phase"] != "prepared":
        raise RuntimeError("La preparación local no terminó; no se abrirá una copia vacía.")
    if Path(data["project"]).resolve() != project or Path(data["data_dir"]).resolve() != path.parent.resolve():
        raise RuntimeError("Esta configuración pertenece a otro proyecto o directorio.")
    UUID(data["instance_id"])
    if data["database"] != DATABASE or data["user"] != DATABASE:
        raise RuntimeError("Solo se admite la base privada del POS integrado.")
    if type(data["port"]) is not int or not 1024 <= data["port"] <= 65535:
        raise RuntimeError("Puerto local inválido.")
    if "backend" in data:
        from .wsl_postgres import validate_backend
        validate_backend(data)
    elif not Path(data["pg_bin"]).is_absolute():
        raise RuntimeError("La ruta de PostgreSQL debe ser absoluta.")
    if any(not isinstance(data[key], str) or len(data[key]) < 40 for key in ("password", "secret_key")):
        raise RuntimeError("Faltan las claves locales generadas durante la preparación.")
    if data.get("activation", "integrated_preview") not in {"database_only", "integrated_preview", "journal_preview"}:
        raise RuntimeError("Modo de activación local desconocido.")
    if data["schema_signature"] != (expected_schema or schema_signature(project)):
        raise RuntimeError("El esquema del proyecto cambió. Se necesita actualizar la copia con respaldo; no se modificó la base. Para el desglose de pagos ejecuta: python scripts/update_integrated_close_payments.py --apply")
    if "backend" not in data and not (path.parent / "postgres/PG_VERSION").is_file():
        raise RuntimeError("Falta el PostgreSQL preparado. No se creará otro encima.")
    return data


def schema_signature(project):
    root = Path(project)
    digest = hashlib.sha256()
    for name in ("mainApp/models.py", "mainApp/hybrid_models.py", "local_pos/models.py"):
        digest.update(name.encode())
        digest.update((root / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def local_database(config):
    return {"ENGINE": "django.db.backends.postgresql", "HOST": "127.0.0.1",
        "PORT": config["port"], "NAME": DATABASE, "USER": DATABASE,
        "PASSWORD": config["password"], "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 3, "options": "-c lock_timeout=5000 -c statement_timeout=15000"}}
