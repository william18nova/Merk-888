"""Actualización local detenida, con respaldo físico y recuperación sin borrados.

No actualiza PostgreSQL, no descarga código y nunca llama a servicios de nube.
El paquete nuevo se prepara aparte; la versión anterior permanece intacta.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile
from uuid import UUID, uuid4

from . import runtime as rt
from .schema_versions import CURRENT, schema_version

FORMAT = "nova-full-local-upgrade-v1"
MARKER = "upgrade-intent.json"


def _plain(path):
    path = Path(path).absolute()
    for item in (path, *path.parents):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise RuntimeError("La actualización no admite enlaces ni uniones de carpetas.")
    return path


def _state(root, *, recovery_config=None):
    root = _plain(root)
    state = rt.read_json(root / "installation.json")
    config = rt.read_json(root / "local.json") if (root / "local.json").exists() else recovery_config
    if not isinstance(config, dict):
        raise RuntimeError("Falta la configuración privada de esta instalación.")
    if (state.get("format") != rt.FORMAT or state.get("instance_id") != config.get("instance_id")
            or state.get("system") != platform.system()
            or config.get("data_dir") != str(root)
            or config.get("database") != rt.DB or config.get("user") != rt.DB
            or (recovery_config is None and not (root / "postgres/PG_VERSION").is_file())):
        raise RuntimeError("La instalación no corresponde a este equipo, ruta o base local.")
    UUID(state["instance_id"])
    return state, config


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _private_fingerprint(root):
    return {p.name: _file_hash(p) for p in sorted(root.glob("*.json")) if p.name != MARKER}


def _save_record(backup, record):
    if len(json.dumps(record).encode()) > 1_900_000:
        raise RuntimeError("El inventario del respaldo supera el límite de esta actualización; requiere revisión.")
    rt.save_json(backup / "upgrade.json", record)


def _probe(root, backup, *, previous_version=None):
    report = backup / ("health-" + uuid4().hex + ".json")
    command = [sys.executable, "-B", "-m", "local_pos.upgrade_probe", str(root), str(report)]
    if previous_version is not None:
        command += ["--from-version", str(previous_version)]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("PG") and k not in {"DJANGO_SETTINGS_MODULE", "NOVA_LOCAL_CONFIG", "DATABASE_URL", "PYTHONPATH"}}
    env["PYTHONUTF8"] = "1"
    with (backup / "verification.log").open("ab") as log:
        result = subprocess.run(command, cwd=rt.ROOT, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, timeout=180)
    if result.returncode:
        raise RuntimeError("Falló la verificación del esquema o las dependencias. Revisa el registro privado de actualización.")
    return rt.read_json(report)


def _stop(pg):
    if pg.status():
        pg.stop()
    if pg.status():
        raise RuntimeError("No se confirmó la parada de PostgreSQL; no se reemplazarán sus archivos.")


def _move(source, destination, *, source_parent, destination_parent):
    source, destination = _plain(source), _plain(destination)
    if (source.parent != source_parent or destination.parent != destination_parent
            or not source.exists() or destination.exists()):
        raise RuntimeError("Movimiento de recuperación fuera de las carpetas verificadas o ambiguo.")
    source.rename(destination)


def _restore_locked(root, backup, record, pg):
    """Reanudable: mueve carpetas verificadas; nunca elimina la versión fallida."""
    _stop(pg)
    if _file_hash(backup / "before.zip") != record["backup_sha256"]:
        raise RuntimeError("El respaldo cambió; se mantiene bloqueada la instalación para revisión.")
    restored = _plain(backup / "restore-data")
    info = record["snapshot"]
    with zipfile.ZipFile(backup / "before.zip") as archive:
        if json.loads(archive.read("backup-manifest.json")) != info:
            raise RuntimeError("El inventario del respaldo cambió; no se restaurará.")
        old = json.loads(archive.read("installation.json"))
    if (info.get("original_root") != str(root) or info.get("instance_id") != record["instance_id"]
            or info.get("package_id") != record["old_id"]):
        raise RuntimeError("El respaldo pertenece a otra identidad o versión.")
    for name in (*info["files"], *info["directories"]):
        path = Path(name)
        if (not path.parts or path.anchor or ".." in path.parts or "\\" in name or ":" in name
                or path.parts[0] in {"runtime.lock", MARKER}):
            raise RuntimeError("Ruta insegura en el inventario del respaldo.")
    # Una interrupción puede haber movido parte de restore-data a su destino.
    # Verificar la unión antes de mover más archivos y antes de activar la base.
    for name, expected in info["files"].items():
        path = restored / name if (restored / name).is_file() else root / name
        if not path.is_file() or _file_hash(_plain(path)) != expected:
            raise RuntimeError("La copia de recuperación está incompleta o dañada; no se activó.")
    record["phase"] = "rolling_back"
    _save_record(backup, record)
    failed = rt.private_dir(backup / "failed-state")
    names = {Path(name).parts[0] for name in (*info["files"], *info["directories"])}
    # La marca de bloqueo y el archivo de instalación se conservan hasta el final.
    for path in list(root.iterdir()):
        if path.name not in names | {"runtime.lock", MARKER}:
            _move(path, failed / path.name, source_parent=root, destination_parent=failed)
    for name in sorted(names - {"installation.json"}):
        source, destination = restored / name, root / name
        if source.exists():
            if destination.exists():
                _move(destination, failed / name, source_parent=root, destination_parent=failed)
            _move(source, destination, source_parent=restored, destination_parent=root)
        elif not destination.exists():
            raise RuntimeError("Falta una carpeta durante la recuperación; conserva todas las copias.")
    # instalación.json conserva phase=upgrading mientras se valida el resto.
    for name, expected in info["files"].items():
        if name != "installation.json" and _file_hash(root / name) != expected:
            raise RuntimeError("La copia restaurada no coincide con el respaldo.")
    for name in info["directories"]:
        _plain(root / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    record["phase"] = "rolled_back"
    _save_record(backup, record)
    rt.save_json(root / "installation.json", old)
    (root / MARKER).unlink(missing_ok=True)


def upgrade(root, *, previous_package, backup_dir):
    root, previous_package, backup = map(_plain, (root, previous_package, backup_dir))
    if backup.parent != root.parent or backup == root or backup.exists():
        raise RuntimeError("El respaldo debe ser una carpeta NUEVA junto a la carpeta de datos, en el mismo disco.")
    if root == rt.ROOT or root in rt.ROOT.parents or rt.ROOT in root.parents:
        raise RuntimeError("La distribución y los datos deben estar separados.")
    current, previous = rt.package_manifest(), rt.package_manifest(previous_package)
    target_version, previous_version = schema_version(current), schema_version(previous)
    if target_version != CURRENT or previous_version > target_version:
        raise RuntimeError("No hay una migración aprobada para ese cambio de versión.")
    if previous["package_id"] == current["package_id"]:
        raise RuntimeError("Ese paquete ya es la versión instalada; no hace falta actualizar.")
    with rt.installation_lock(root):
        state, config = _state(root)
        if state.get("phase") != "ready" or state.get("package_id") != previous["package_id"]:
            raise RuntimeError("La versión anterior no coincide con la instalación o hay una actualización incompleta.")
        if any((root / name).exists() for name in (MARKER, "pairing-intent.json", "handoff-intent.json", "next-session-intent.json")):
            raise RuntimeError("Completa primero la transición pendiente; no se actualizará una sesión a medias.")
        pg = rt.Postgres(root, state["pg_bin"], config)
        if pg.status():
            raise RuntimeError("Detén completamente el POS antes de actualizar.")
        for path in root.rglob("*"):
            _plain(path)
        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        if shutil.disk_usage(root.parent).free < 2 * size + 256 * 1024 ** 2:
            raise RuntimeError("No hay espacio suficiente para el respaldo y la recuperación sin borrar datos.")
        rt.private_dir(backup)
        rt._backup_locked(root, backup / "before.zip", state)
        snapshot = rt._unpack_backup(backup / "restore-data", backup / "before.zip",
            confirm_instance=state["instance_id"], original_root=root, package_id=previous["package_id"])
        rt.save_json(backup / "original-state.json", state)
        record = {"format": FORMAT, "id": str(uuid4()), "root": str(root), "system": platform.system(),
            "instance_id": state["instance_id"], "old_package": str(previous_package),
            "old_id": previous["package_id"], "new_id": current["package_id"],
            "backup_sha256": _file_hash(backup / "before.zip"), "snapshot": snapshot, "phase": "prepared"}
        _save_record(backup, record)
        rt.save_json(root / MARKER, {"backup": str(backup), "id": record["id"]})
        rt.save_json(root / "installation.json", {**state, "phase": "upgrading"})
        try:
            pg.start()
            record["health"] = _probe(root, backup, previous_version=previous_version)
            _stop(pg)
            new_state = {**state, "phase": "ready", "package_id": current["package_id"],
                         "schema_version": target_version, "last_upgrade": record["id"]}
            rt.save_json(root / "installation.json", new_state)
            record["private_files"] = _private_fingerprint(root)
            record["phase"] = "committed"
            _save_record(backup, record)
            (root / MARKER).unlink()
        except BaseException:
            try:
                _restore_locked(root, backup, record, pg)
            except Exception:
                raise RuntimeError(f"Actualización incompleta. No abras el POS. Conserva el respaldo privado y usa rollback-upgrade con: {backup}") from None
            raise RuntimeError("La actualización no pasó la verificación. Se recuperó la versión anterior sin perder pendientes.") from None
    print(f"Actualización verificada. Abre el POS desde el paquete nuevo. Respaldo privado: {backup}")
    return record


def rollback(root, *, backup_dir, confirm_instance):
    root, backup = map(_plain, (root, backup_dir))
    if backup.parent != root.parent or backup == root:
        raise RuntimeError("El respaldo debe pertenecer a esta instalación y al mismo disco.")
    record = rt.read_json(backup / "upgrade.json")
    if (record.get("format") != FORMAT or record.get("root") != str(root)
            or record.get("system") != platform.system()
            or record.get("instance_id") != str(UUID(confirm_instance))
            or record.get("new_id") != rt.package_manifest()["package_id"]):
        raise RuntimeError("La recuperación no corresponde a esta instalación, paquete o identidad.")
    previous = rt.package_manifest(_plain(record["old_package"]))
    if previous["package_id"] != record["old_id"]:
        raise RuntimeError("La distribución anterior cambió; no se recuperará una versión mezclada.")
    with rt.installation_lock(root):
        if _file_hash(backup / "before.zip") != record["backup_sha256"]:
            raise RuntimeError("El respaldo cambió; no se recuperarán archivos sin verificar.")
        with zipfile.ZipFile(backup / "before.zip") as archive:
            original_config = json.loads(archive.read("local.json"))
        state, config = _state(root, recovery_config=original_config)
        if state["instance_id"] != record["instance_id"]:
            raise RuntimeError("La identidad de la base cambió; no se restaurará otra instalación.")
        pg = rt.Postgres(root, state["pg_bin"], config)
        if pg.status():
            raise RuntimeError("Detén el POS antes de volver a la versión anterior.")
        marker = root / MARKER
        if marker.exists() and rt.read_json(marker) != {"backup": str(backup), "id": record["id"]}:
            raise RuntimeError("Hay otra actualización en curso.")
        if (record["phase"] == "rolled_back" and state.get("phase") == "ready"
                and state.get("package_id") == record["old_id"]):
            marker.unlink(missing_ok=True)
            print("La versión anterior ya estaba recuperada; no se cambiaron datos.")
            return
        if record["phase"] == "prepared" and state.get("phase") == "ready" and state.get("package_id") == record["old_id"]:
            marker.unlink(missing_ok=True)  # No se llegó a modificar la base.
            record["phase"] = "rolled_back"
            _save_record(backup, record)
            return
        if not marker.exists():
            if state.get("package_id") != record["new_id"] or record.get("phase") != "committed":
                raise RuntimeError("El respaldo no es la última actualización aplicada.")
            if _private_fingerprint(root) != record["private_files"]:
                raise RuntimeError("La instalación cambió después de actualizar. No se puede retroceder sin conciliación.")
            try:
                pg.start()
                health = _probe(root, backup)
            finally:
                _stop(pg)
            if health != record["health"]:
                raise RuntimeError("Hay información nueva después de actualizar. Se bloqueó el retroceso para no perderla.")
        rt.save_json(root / MARKER, {"backup": str(backup), "id": record["id"]})
        rt.save_json(root / "installation.json", {**state, "phase": "upgrading"})
        _restore_locked(root, backup, record, pg)
    print("Versión anterior recuperada. Usa su lanzador; la versión fallida se conservó para revisión.")
