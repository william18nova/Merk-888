import json
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from pathlib import Path
from pos_shared.protocol import canonical


def default_directory():
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "NovaPOS" / "data"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "novapos"


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        if (self.directory / "RECOVERY-REVIEW.json").exists() or (self.directory / "recovery.sqlite3").exists():
            raise RuntimeError("Esta carpeta es una recuperación para revisión, no una caja activa. No se enviará ni imprimirá ninguna venta.")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "operations.sqlite3"
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise RuntimeError("Los datos pertenecen a una versión más nueva. No se pueden abrir con este programa.")
            if version and db.execute("SELECT 1 FROM meta WHERE key='recovery_review_required'").fetchone():
                raise RuntimeError("Respaldo recuperado pendiente de conciliación. No se puede activar como caja.")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, session TEXT NOT NULL,
                  sequence INTEGER NOT NULL, payload TEXT NOT NULL, receipt TEXT NOT NULL,
                  state TEXT NOT NULL DEFAULT 'pending', result TEXT NOT NULL DEFAULT '{}',
                  error TEXT NOT NULL DEFAULT '', UNIQUE(session, sequence));
                CREATE TABLE IF NOT EXISTS print_jobs(
                  id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
                  payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                  message TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                PRAGMA user_version=2;
            """)
        if os.name != "nt":
            os.chmod(self.directory, 0o700)
            os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, canonical(value)))

    def products(self):
        with self.connect() as db:
            return {str(row["id"]): json.loads(row["value"]) for row in db.execute("SELECT * FROM products")}

    def merge_catalog(self, result):
        with self.connect() as db:
            for row in result["updates"]:
                db.execute("INSERT INTO products VALUES(?,?) ON CONFLICT(id) DO UPDATE SET value=excluded.value", (row["id"], canonical(row)))
            for pid in result["deleted"]:
                db.execute("DELETE FROM products WHERE id=?", (pid,))

    def operation(self, operation_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE id=?", (operation_id,)).fetchone()
            return dict(row) if row else None

    def enqueue(self, payload, receipt):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT INTO operations(id,session,sequence,payload,receipt) VALUES(?,?,?,?,?)",
                       (payload["operation_id"], payload["session_id"], payload["sequence"], canonical(payload), receipt))

    def last_sequence(self, session):
        with self.connect() as db:
            return db.execute("SELECT COALESCE(MAX(sequence),0) FROM operations WHERE session=?", (session,)).fetchone()[0]

    def pending(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM operations WHERE state!='accepted' ORDER BY rowid")]

    def mark(self, operation_id, state, result=None, error=""):
        with self.connect() as db:
            db.execute("UPDATE operations SET state=?, result=?, error=? WHERE id=?", (state, canonical(result or {}), error, operation_id))

    def history(self, limit=30, *, session=None):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM operations WHERE session=? ORDER BY rowid DESC LIMIT ?", (session, limit))]

    def backup(self):
        # Copia consistente; copiar solo el .sqlite3 abierto podría perder WAL.
        target = self.directory / "backup.sqlite3"
        fd, name = tempfile.mkstemp(prefix=".backup-", suffix=".sqlite3", dir=self.directory)
        os.close(fd)
        temporary = Path(name)
        try:
            with self.connect() as source:
                with closing(sqlite3.connect(temporary)) as dest:
                    source.backup(dest)
                    if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("No se pudo verificar la copia local; se conserva la anterior.")
            with temporary.open("r+b") as saved:
                os.fsync(saved.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def print_job(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM print_jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def print_jobs(self, *, operation_id=None, state=None):
        where, params = [], []
        if operation_id is not None:
            where.append("operation_id=?"); params.append(operation_id)
        if state is not None:
            where.append("state=?"); params.append(state)
        sql = "SELECT * FROM print_jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql + " ORDER BY rowid", params)]

    def enqueue_print(self, job_id, operation_id, payload):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO print_jobs(id,operation_id,payload) VALUES(?,?,?)",
                       (job_id, operation_id, canonical(payload)))
        return self.print_job(job_id)

    def mark_print(self, job_id, state, message):
        with self.connect() as db:
            db.execute("UPDATE print_jobs SET state=?,message=? WHERE id=?", (state, message, job_id))

    def recover_inflight_prints(self):
        with self.connect() as db:
            db.execute("UPDATE print_jobs SET state='uncertain',message=? WHERE state='sending'",
                       ("La aplicación se cerró durante el envío. Revisa si salió papel antes de pedir una copia.",))
