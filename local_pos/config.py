"""Configuración explícita local; no acepta DSN, hosts remotos ni secretos cloud."""
import json
import os
from pathlib import Path
from uuid import UUID


def load_config():
    path = Path(os.environ.get("NOVA_LOCAL_CONFIG", ""))
    if not path.is_absolute() or not path.is_file():
        raise RuntimeError("Indica NOVA_LOCAL_CONFIG con el archivo privado de esta instalación local.")
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {"application", "instance_id", "secret_key", "database", "port", "user", "password", "data_dir", "mode"}
    if not isinstance(data, dict) or set(data) != required:
        raise RuntimeError("Configuración local inválida; no se aceptan hosts, DSN ni credenciales adicionales.")
    if data["application"] != "nova-full-local-development-v1" or data["mode"] != "development_readonly":
        raise RuntimeError("El runtime completo aún requiere el modo de desarrollo de solo consulta.")
    UUID(data["instance_id"])
    if data["database"] != "nova_full_local_lab" or data["user"] != "nova_full_local_lab":
        raise RuntimeError("Esta etapa solo admite una base local de laboratorio identificada.")
    if type(data["port"]) is not int or not 1024 <= data["port"] <= 65535:
        raise RuntimeError("Puerto local inválido.")
    if (not isinstance(data["secret_key"], str) or len(data["secret_key"]) < 40
            or not isinstance(data["password"], str) or len(data["password"]) < 24):
        raise RuntimeError("La instalación debe generar sus propias claves aleatorias.")
    directory = Path(data["data_dir"])
    if not directory.is_absolute() or directory.resolve() != path.parent.resolve():
        raise RuntimeError("El archivo debe pertenecer al directorio privado de esta instalación.")
    return data
