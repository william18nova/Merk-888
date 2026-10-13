"""One bounded background probe; no business query is retried on another DB."""
import atexit
import threading
import time

from django.db import connections
from .connectivity import Health
from .integrated_policy import CLOUD_CONNECT_TIMEOUT

PROBE_INTERVAL = 3
# A probe may spend 6s connecting and 3s executing. Keep the previous completed
# result valid while the next bounded probe runs, not just for the old 6s.
MAX_RESULT_AGE = 20


def classify_error(error):
    code = getattr(error, "pgcode", None)
    if code and (code.startswith("08") or code in {"57P01", "57P02", "57P03"}):
        return Health.DISCONNECTED
    message = str(error).lower()
    if any(text in message for text in ("password authentication failed", "certificate verify failed", "no pg_hba.conf entry")):
        return Health.REJECTED
    if any(text in message for text in ("timeout expired", "connection refused", "network is unreachable",
        "no route to host", "server closed the connection", "connection timed out", "could not translate host name",
        "ssl syscall error", "eof detected", "connection reset by peer")):
        return Health.DISCONNECTED
    return Health.REJECTED


class CloudMonitor:
    def __init__(self):
        self.health = Health.UNKNOWN
        self.checked_at = 0
        self.started = False
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.stop = threading.Event()

    def _probe(self):
        import psycopg2
        params = connections["default"].get_connection_params()
        params.update(connect_timeout=CLOUD_CONNECT_TIMEOUT, keepalives=1, keepalives_idle=5,
                      keepalives_interval=2, keepalives_count=2, tcp_user_timeout=10000)
        params["options"] = params.get("options", "") + " -c statement_timeout=3000"
        connection = None
        try:
            connection = psycopg2.connect(**params)
            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('solicitudes_pos') IS NOT NULL")
                return Health.AVAILABLE if cursor.fetchone()[0] else Health.REJECTED
        except psycopg2.Error as exc:
            return classify_error(exc)
        finally:
            if connection is not None:
                connection.close()

    def _run(self):
        while not self.stop.is_set():
            try:
                value = self._probe()
            except Exception:
                value = Health.REJECTED
            with self.lock:
                self.health, self.checked_at = value, time.monotonic()
            self.ready.set()
            self.stop.wait(PROBE_INTERVAL)

    def snapshot(self):
        with self.lock:
            if not self.started:
                self.started = True
                threading.Thread(target=self._run, daemon=True, name="nova-cloud-health").start()
                atexit.register(self.stop.set)
        self.ready.wait(CLOUD_CONNECT_TIMEOUT + 3.5)
        with self.lock:
            return self.health if time.monotonic() - self.checked_at < MAX_RESULT_AGE else Health.UNKNOWN


monitor = CloudMonitor()
