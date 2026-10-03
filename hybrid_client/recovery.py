"""Reactivar solo tras aprobación en nube; nunca modifica la carpeta original."""
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile
from uuid import UUID

from pos_shared.protocol import ProtocolError, canonical, fingerprint
from .backup import TABLES, REVIEW_MARKER, validate
from .client import Client, cloud_url
from .store import Store

STATE_KEY = "recovery_client_state"


class Recovery:
    def __init__(self, directory, url, *, transport=None):
        self.directory = Path(directory).resolve()
        self.path = self.directory / "recovery.sqlite3"
        if not self.path.is_file() or not (self.directory / REVIEW_MARKER).is_file():
            raise ProtocolError("Primero restaura el respaldo en una carpeta nueva para revisión.")
        self.url = cloud_url(url)
        self.transport = transport
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA trusted_schema=OFF")
            rows = {table: [dict(r) for r in db.execute(f"SELECT {','.join(columns)} FROM {table} ORDER BY rowid")] for table, columns in TABLES.items()}
        self.meta = {r["key"]: json.loads(r["value"]) for r in rows["meta"]}
        review = self.meta.get("recovery_review_required")
        if not isinstance(review, dict) or self.meta.get("config", {}).get("url") != self.url:
            raise ProtocolError("La dirección no coincide con el POS del respaldo. No se enviaron datos.")
        rows["meta"] = [r for r in rows["meta"] if r["key"] not in {"recovery_review_required", STATE_KEY}]
        self.document = {"format": "novapos-backup", "version": 1, "database_version": 2,
                         "created_at": review["created_at"], "client_version": review["version"], "tables": rows}
        validate(self.document)
        self.bundle = {"device_id": self.meta["config"]["device_id"], "backup_created_at": review["created_at"],
                       "operations": [{"state": r["state"], "payload": json.loads(r["payload"])} for r in rows["operations"]]}
        if len(self.bundle["operations"]) > 1000 or len(canonical(self.bundle).encode()) > 1800000:
            raise ProtocolError("Este respaldo supera el límite de recuperación del piloto. Solicita revisión asistida; no recortes sus ventas.")
        self.state = self.meta.get(STATE_KEY)
        if self.state and self.state.get("manifest_hash") != fingerprint(self.bundle):
            raise ProtocolError("El respaldo cambió desde el primer intento. Conserva la copia y solicita revisión.")

    def save(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("PRAGMA synchronous=FULL")
            with db:
                db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (STATE_KEY, canonical(self.state)))

    def remote(self, action, data):
        if self.transport:
            return self.transport(action, data)
        # Reutilizar el transporte HTTPS sin guardar datos/secretos en el navegador.
        class Config:
            def get(_self, key):
                return {"url": self.url} if key == "config" else None
        sender = object.__new__(Client)
        sender.transport, sender.store = None, Config()
        return sender.remote("recovery/" + action, data, anonymous=True)

    def prepare(self, code):
        try:
            record_id, _ = code.split(".", 1)
            record_id = str(UUID(record_id))
        except (ValueError, AttributeError):
            raise ProtocolError("Introduce el código completo entregado por el Web Master.") from None
        if not self.state or self.state["recovery_id"] != record_id:
            # Un código reemitido crea una credencial diferente. La anterior nunca
            # se reactiva ni se copia a la instalación nueva.
            self.state = {"recovery_id": record_id, "secret": secrets.token_hex(32),
                          "manifest_hash": fingerprint(self.bundle), "prepared": False}
            self.save()
        response = self.remote("prepare", {"code": code, "secret": self.state["secret"], "manifest": self.bundle})
        if response.get("recovery_id") != record_id or response.get("state") not in {"review", "approved"}:
            raise ProtocolError("No llegó una confirmación válida de la revisión. Conserva la carpeta.")
        self.state["prepared"] = True
        self.save()
        return response

    def finish(self):
        if not self.state or not self.state.get("prepared"):
            raise ProtocolError("Primero presenta el respaldo con el código de recuperación.")
        result = self.remote("finish", {"recovery_id": self.state["recovery_id"], "secret": self.state["secret"], "manifest": self.bundle})
        if result.get("state") == "review":
            return result
        if (result.get("state") != "completed" or result.get("recovery_id") != self.state["recovery_id"]
                or result.get("device_id") != self.bundle["device_id"]):
            raise ProtocolError("La nube no confirmó esta recuperación. No se habilitó una caja.")
        acknowledgements = result.get("acknowledgements")
        expected = {r["payload"]["operation_id"] for r in self.bundle["operations"]}
        if not isinstance(acknowledgements, dict) or set(acknowledgements) != expected:
            raise ProtocolError("Faltan confirmaciones del servidor. Reintenta la misma recuperación.")
        for op_id, ack in acknowledgements.items():
            if not isinstance(ack, dict) or ack.get("operation_id") != op_id or ack.get("status") != "accepted":
                raise ProtocolError("Una confirmación no es válida. No se activó la caja.")
        destination = self.activate(result)
        return {"state": "completed", "directory": str(destination), "summary": result["summary"]}

    def activate(self, result):
        destination = self.directory / "pos-recuperado"
        if destination.exists():
            if destination.is_symlink():
                raise ProtocolError("La carpeta de destino no es válida.")
            target = destination / "operations.sqlite3"
            if not target.is_file() or target.is_symlink():
                raise ProtocolError("Ya existe otro POS en el destino. No se reemplazó.")
            # Un reintento solo inspecciona; nunca inicializa/migra una carpeta
            # que el usuario hubiera creado con otros datos.
            with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("PRAGMA trusted_schema=OFF")
                values = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM meta WHERE key IN ('config','recovered_from')")}
            if (values.get("recovered_from") != self.state["recovery_id"]
                    or values.get("config", {}).get("secret") != self.state["secret"]):
                raise ProtocolError("Ya existe otro POS en el destino. No se reemplazó.")
            return destination
        with tempfile.TemporaryDirectory(prefix=".preparando-pos-", dir=self.directory) as temp:
            fresh = Store(temp)
            # Normal schema/defaults, no plaintext copy of the old credentials,
            # PIN, stale catalog or print-agent token. A fresh login is required.
            with fresh.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                config = {"url": self.url, "device_id": result["device_id"], "secret": self.state["secret"],
                          "name": result["name"], "point": result["point"]}
                db.executemany("INSERT INTO meta VALUES(?,?)", [("config", canonical(config)),
                    ("recovered_from", canonical(self.state["recovery_id"])), ("ready", "false"), ("session", "null")])
                for row in self.document["tables"]["operations"]:
                    ack = result["acknowledgements"][row["id"]]
                    db.execute("INSERT INTO operations(id,session,sequence,payload,receipt,state,result,error) VALUES(?,?,?,?,?,'accepted',?,'')",
                        (row["id"], row["session"], row["sequence"], row["payload"], ack.get("receipt_text") or row["receipt"], canonical(ack)))
                for row in self.document["tables"]["print_jobs"]:
                    db.execute("INSERT INTO print_jobs(id,operation_id,payload,state,message,created_at) VALUES(?,?,?,?,?,?)",
                        (row["id"], row["operation_id"], row["payload"], "uncertain" if row["state"] in {"queued", "sending"} else row["state"],
                         "Recuperado: no reimprimir automáticamente. Revisar el papel.", row["created_at"]))
            # Todos los archivos se preparan fuera de la ruta de uso y solo se
            # publican al terminar. ProcessLock serializa intentos en esta carpeta.
            os.rename(temp, destination)
        return destination
