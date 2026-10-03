"""Esquemas locales aprobados. Cambiar modelos exige una migración explícita."""
import hashlib
import json

SCHEMA_FILES = ("mainApp/models.py", "mainApp/hybrid_models.py", "local_pos/models.py")
APPROVED = {
    "ba849b534f4a1ac967258cf3197fa1e22c3f9a0d2a104dd60e53b39b5fde9a3d": 1,
    "6d177c47d8751b8359466a12e2aa076e59f08f02ac5d6e6d6805ba80d4694f0c": 2,
}
CURRENT = 2


def schema_version(manifest):
    files = manifest.get("files", {})
    if any(name not in files for name in SCHEMA_FILES):
        raise RuntimeError("Faltan archivos del esquema local en el manifiesto.")
    signature = hashlib.sha256(json.dumps({name: files[name] for name in SCHEMA_FILES}, sort_keys=True).encode()).hexdigest()
    version = APPROVED.get(signature)
    if version is None:
        raise RuntimeError("Esquema local desconocido. Requiere una migración preparada y probada; no se actualizaron datos.")
    return version
