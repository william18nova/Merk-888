"""Respaldos portables autenticados. Restaurar nunca activa una segunda caja.

Solo se exportan datos de tablas conocidas, no SQL ejecutable. La recuperación
crea una base de revisión independiente y bloqueada hasta conciliación asistida.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import sqlite3
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from . import VERSION
from pos_shared.protocol import canonical, ProtocolError

MAGIC = b"NOVAPOS-BACKUP\x00\x01"
MAX_BYTES = 128 * 1024 * 1024
TABLES = {
    "meta": ("key", "value"),
    "products": ("id", "value"),
    "operations": ("id", "session", "sequence", "payload", "receipt", "state", "result", "error"),
    "print_jobs": ("id", "operation_id", "payload", "state", "message", "created_at"),
}
REVIEW_MARKER = "RECOVERY-REVIEW.json"


def _key(password, salt):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ProtocolError("Usa una contraseña de respaldo de 12 a 128 caracteres.")
    # Parámetros fijos: un archivo externo no puede escoger un coste desmedido.
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode("utf-8"))


def snapshot(path):
    """Una transacción de lectura incluye WAL sin interrumpir otros escritores."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ProtocolError("No hay una base local para respaldar en esa carpeta.")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)) as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise ProtocolError("Versión de datos no compatible con este respaldo.")
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ProtocolError("La base local necesita revisión; conserva sus archivos originales.")
        tables = {}
        for table, columns in TABLES.items():
            # Nombres constantes, nunca proceden del archivo o de la solicitud.
            tables[table] = [dict(row) for row in db.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid")]
    document = {"format": "novapos-backup", "version": 1, "database_version": 2,
                "client_version": VERSION, "created_at": datetime.now(timezone.utc).isoformat(), "tables": tables}
    validate(document)
    return document


def validate(document):
    """Validar antes de crear archivos o importar cualquier fila."""
    try:
        if (document["format"] != "novapos-backup" or document["version"] != 1
                or document["database_version"] != 2 or set(document["tables"]) != set(TABLES)):
            raise ValueError()
        created = datetime.fromisoformat(document["created_at"])
        if created.tzinfo is None or not isinstance(document["client_version"], str):
            raise ValueError()
        ids = {}
        for table, columns in TABLES.items():
            rows = document["tables"][table]
            if not isinstance(rows, list):
                raise ValueError()
            ids[table] = set()
            for row in rows:
                if not isinstance(row, dict) or set(row) != set(columns):
                    raise ValueError()
                for column in columns:
                    expected = int if column in {"sequence"} or (column == "id" and table == "products") else str
                    if type(row[column]) is not expected:
                        raise ValueError()
                identity = row["key" if table == "meta" else "id"]
                if identity in ids[table]:
                    raise ValueError()
                ids[table].add(identity)
                for field in ("value", "payload", "result"):
                    if field in row:
                        json.loads(row[field])
        meta = {row["key"]: json.loads(row["value"]) for row in document["tables"]["meta"]}
        if meta.get("recovery_review_required"):
            raise ValueError()
        config = meta.get("config") or {}
        if not isinstance(config, dict):
            raise ValueError()
        if config.get("device_id"):
            UUID(config["device_id"])
        seen_sequence = set()
        for row in document["tables"]["operations"]:
            UUID(row["id"]); UUID(row["session"])
            payload = json.loads(row["payload"])
            pair = (row["session"], row["sequence"])
            if (pair in seen_sequence or row["sequence"] < 1
                    or row["state"] not in {"pending", "accepted", "conflict"}
                    or payload["operation_id"] != row["id"] or payload["session_id"] != row["session"]
                    or type(payload["sequence"]) is not int or payload["sequence"] != row["sequence"]):
                raise ValueError()
            seen_sequence.add(pair)
            result = json.loads(row["result"])
            if row["state"] == "accepted" and (result.get("status") != "accepted" or result.get("operation_id") != row["id"]):
                raise ValueError()
        for row in document["tables"]["print_jobs"]:
            if row["operation_id"] not in ids["operations"] or row["state"] not in {"queued", "sending", "sent", "uncertain", "failed"}:
                raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
        raise ProtocolError("El respaldo no tiene una estructura válida. No se modificó la caja.") from None


def summary(document):
    """Resumen permitido: nunca devuelve tokens, claves, recibos ni datos personales."""
    states = {state: 0 for state in ("pending", "accepted", "conflict")}
    for row in document["tables"]["operations"]:
        states[row["state"]] += 1
    return {"created_at": document["created_at"], "version": document["client_version"],
            "products": len(document["tables"]["products"]), "operations": sum(states.values()),
            **states, "print_jobs": len(document["tables"]["print_jobs"])}


def encrypt(document, password):
    validate(document)
    plain = canonical(document).encode("utf-8")
    if len(plain) > MAX_BYTES - 128:
        raise ProtocolError("El respaldo supera el tamaño del piloto. Solicita una copia asistida; no borres datos.")
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    header = MAGIC + salt + nonce
    return header + AESGCM(_key(password, salt)).encrypt(nonce, plain, header)


def decrypt(content, password):
    header_size = len(MAGIC) + 28
    if not header_size + 16 <= len(content) <= MAX_BYTES or not content.startswith(MAGIC):
        raise ProtocolError("No es un respaldo NovaPOS compatible o está incompleto.")
    header = content[:header_size]
    salt, nonce = header[len(MAGIC):len(MAGIC)+16], header[-12:]
    try:
        raw = AESGCM(_key(password, salt)).decrypt(nonce, content[header_size:], header)
        document = json.loads(raw)
    except InvalidTag:
        raise ProtocolError("Contraseña incorrecta o respaldo alterado/incompleto. No se restauró nada.") from None
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("El contenido del respaldo no es válido. No se restauró nada.") from None
    validate(document)
    return document


def read_backup(path, password):
    with Path(path).open("rb") as source:
        content = source.read(MAX_BYTES + 1)
    return decrypt(content, password)


def write_new(path, content):
    """Crear sin sobrescribir. Una interrupción nunca sustituye el respaldo anterior."""
    path = Path(path)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ProtocolError("Ya existe ese archivo. Elige otro nombre; el respaldo anterior se conserva.") from None
    try:
        with os.fdopen(fd, "wb") as dest:
            dest.write(content)
            dest.flush()
            os.fsync(dest.fileno())
    except BaseException:
        # Solo se elimina el archivo que acabamos de crear, nunca el anterior.
        path.unlink(missing_ok=True)
        raise
    return path


def restore_for_review(document, directory):
    """Importación parametrizada a una carpeta NUEVA, nunca sobre el POS activo."""
    validate(document)
    directory = Path(directory).absolute()
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        raise ProtocolError("La recuperación necesita una carpeta nueva. No se sobrescribió ningún dato.") from None
    info = {**summary(document), "review_required": True,
            "warning": "NO ACTIVAR COMO CAJA. Cotejar UUID y secuencias con la nube y retirar el equipo anterior antes de recuperar."}
    write_new(directory / REVIEW_MARKER, canonical(info).encode("utf-8"))
    # Crear el marcador antes que la base: incluso una interrupción queda bloqueada.
    target = directory / "recovery.sqlite3"
    write_new(target, b"")
    with closing(sqlite3.connect(target)) as db:
        with db:
            db.execute("PRAGMA synchronous=FULL")
            for table, columns in TABLES.items():
                definitions = [f"{c} {'INTEGER' if c == 'sequence' or (c == 'id' and table == 'products') else 'TEXT'} NOT NULL" for c in columns]
                key = "key" if table == "meta" else "id"
                definitions.append(f"PRIMARY KEY ({key})")
                if table == "operations":
                    definitions.append("UNIQUE(session,sequence)")
                db.execute(f"CREATE TABLE {table}({','.join(definitions)})")
                db.executemany(f"INSERT INTO {table} VALUES({','.join('?' for _ in columns)})",
                               ([row[c] for c in columns] for row in document["tables"][table]))
            db.execute("INSERT OR REPLACE INTO meta VALUES('recovery_review_required',?)", (canonical(info),))
            # El papel pudo salir antes de perder el equipo. Nunca reproducir la cola.
            db.execute("UPDATE print_jobs SET state='uncertain',message=? WHERE state IN ('queued','sending')",
                       ("Recuperado de respaldo: revisar papel; no reenviar automáticamente.",))
            db.execute("PRAGMA user_version=2")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ProtocolError("La copia recuperada necesita revisión. La caja original no se modificó.")
    return info
