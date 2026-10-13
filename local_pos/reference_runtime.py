"""One background refresher; never delay/retry the cashier's cloud response."""
import atexit
import logging
import threading
import time

from django.conf import settings
from django.db import connections

logger = logging.getLogger(__name__)


class ReferenceWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.actor = self.point = None
        self.lease_until = self.next_attempt = 0
        self.failures = 0
        self.busy = False
        self.queued = False
        self.message = "Prepara la copia inicial para este usuario y caja."

    def _start(self):
        if not self.thread:
            self.thread = threading.Thread(target=self._run, name="nova-reference-copy", daemon=True)
            atexit.register(self.stop.set)
            self.thread.start()

    def observe(self, actor, *, changed=False):
        with self.lock:
            if self.actor != actor:
                self.actor, self.point = actor, None
                self.next_attempt = 0
            self.lease_until = time.monotonic() + 15 * 60
            if changed:
                self.next_attempt = min(self.next_attempt, time.monotonic() + 5)
            self._start()
        self.wake.set()

    def request(self, actor, point):
        with self.lock:
            if self.busy or self.queued:
                return False
            self.actor, self.point = actor, point
            self.lease_until = time.monotonic() + 15 * 60
            self.next_attempt = 0
            self.message = "Descargando copia; puedes seguir trabajando."
            self.queued = True
            self._start()
        self.wake.set()
        return True

    def pause(self):
        with self.lock:
            self.lease_until = 0
            self.queued = False

    def status(self):
        with self.lock:
            return {"busy": self.busy or self.queued, "message": self.message}

    def _run(self):
        from .integrated_replica import synchronize, SOURCE_PREFIX
        from .models import ReplicaState
        from .replica import ReplicaError
        while not self.stop.is_set():
            self.wake.wait(2)
            self.wake.clear()
            with self.lock:
                if not self.actor or time.monotonic() >= self.lease_until or time.monotonic() < self.next_attempt:
                    continue
                actor, point = self.actor, self.point
                self.busy = True
                self.queued = False
            try:
                if point is None:
                    state = ReplicaState.objects.using("offline").filter(
                        node_id=settings.LOCAL_CONFIG["instance_id"], local_user_id=actor,
                        server__startswith=SOURCE_PREFIX).first()
                    if not state:
                        with self.lock:
                            self.next_attempt = time.monotonic() + 60
                        continue
                    point = state.scope.get("point_id")
                # No lease is extended by this worker: only authenticated online
                # activity renews it. Logout stops automatic renewal.
                with self.lock:
                    if self.actor != actor or time.monotonic() >= self.lease_until:
                        continue
                synchronize(actor, point)
                with self.lock:
                    self.failures = 0
                    self.message = "Copia actualizada."
                    self.next_attempt = time.monotonic() + 60
            except Exception as exc:
                with self.lock:
                    self.failures += 1
                    self.next_attempt = time.monotonic() + min(300, 15 * 2 ** min(self.failures - 1, 5))
                    self.message = str(exc) if isinstance(exc, ReplicaError) else "No se pudo actualizar; se conserva la copia anterior. Se reintentará automáticamente."
                # No DSN, passwords, user hashes, SQL or provider payloads in logs.
                logger.warning("POS: actualización de referencia pendiente (%s).", type(exc).__name__)
            finally:
                connections.close_all()
                with self.lock:
                    self.busy = False


worker = ReferenceWorker()


class ReferenceRefreshMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not getattr(settings, "NOVA_REFERENCE_ENABLED", False):
            return response
        from .request_context import offline_request
        if offline_request():
            return response
        try:
            name = getattr(getattr(request, "resolver_match", None), "url_name", None)
            if name == "logout":
                worker.pause()
            elif request.user.is_authenticated and response.status_code < 400:
                worker.observe(request.user.pk, changed=request.method not in {"GET", "HEAD", "OPTIONS"})
        except Exception:
            # Never turn an already committed cloud sale into a failed response.
            logger.warning("POS: no se programó la actualización de referencia.")
        return response


class ReferenceRouter:
    def db_for_read(self, model, **hints):
        return "offline" if model._meta.app_label == "local_pos" else None

    db_for_write = db_for_read

    def allow_migrate(self, db, app_label, **hints):
        return False if db == "offline" or app_label == "local_pos" else None


def configure_reference(namespace):
    """Reference-only mode: do not move sessions or activate offline sales."""
    namespace["NOVA_REFERENCE_ENABLED"] = True
    namespace["HYBRID_LOCAL_ENABLED"] = True
    namespace["INSTALLED_APPS"] = ["local_pos", *namespace["INSTALLED_APPS"]]
    namespace["DATABASE_ROUTERS"] = ["local_pos.reference_runtime.ReferenceRouter", *namespace.get("DATABASE_ROUTERS", [])]
    namespace["CACHES"] = {**namespace.get("CACHES", {}), "offline": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "nova-reference"}}
    middleware = list(namespace["MIDDLEWARE"])
    index = middleware.index("django.contrib.auth.middleware.AuthenticationMiddleware")
    middleware.insert(index + 1, "local_pos.reference_runtime.ReferenceRefreshMiddleware")
    namespace["MIDDLEWARE"] = middleware
