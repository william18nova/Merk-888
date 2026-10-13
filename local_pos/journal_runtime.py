"""Recover online ACKs, upload offline sales, then refresh the private copy."""
import atexit
import logging
import threading
import time

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)


class JournalWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        with self.lock:
            if self.thread and self.thread.is_alive():
                return
            thread = threading.Thread(target=self.run, daemon=True, name="nova-original-confirmations")
            thread.start()
            self.thread = thread
            atexit.register(self.stop.set)

    def run(self):
        from .integrated_journal import reconcile_pending
        from .integrated_offline_sales import flush
        from .integrated_replica import synchronize
        from .models import ReplicaState, LocalCommand
        failures = 0
        refreshed = 0
        while not self.stop.is_set():
            try:
                reconcile_pending()
                flush()
                state = ReplicaState.objects.using("offline").filter(node_id=settings.LOCAL_CONFIG["instance_id"]).first()
                if (state and state.active_id and (state.scope.get("refresh_required") or time.monotonic() - refreshed >= 60)
                        and not LocalCommand.objects.using("offline").filter(node_id=state.node_id).exclude(state="accepted").exists()):
                    synchronize(state.local_user_id, state.scope["point_id"])
                    refreshed = time.monotonic()
                failures = 0
            except Exception as exc:
                failures += 1
                logger.warning("POS: sincronización pendiente (%s); se conservan las ventas y sus referencias.", type(exc).__name__)
            finally:
                connections.close_all()
            self.stop.wait(min(30, 2 ** min(failures + 1, 5)))


worker = JournalWorker()


class JournalRecoveryMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if getattr(settings, "NOVA_INTEGRATED_JOURNAL_ENABLED", False):
            try:
                worker.start()
            except Exception:
                logger.warning("POS: recuperación pendiente; se conservan las referencias.")
        return self.get_response(request)
