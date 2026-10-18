"""Detect deployments between messages, never interrupt an active transaction."""
import time
from pathlib import Path


class SourceWatcher:
    """Pause during changing source files, then replace the single worker process.

    Only metadata is read: no settings import, credentials, SQL or external API.
    The caller must poll outside message processing/transactions.
    """

    def __init__(self, root, *, interval=5.0, settle=5.0, clock=time.monotonic):
        self.root = Path(root)
        self.interval, self.settle, self.clock = interval, settle, clock
        self.baseline = self.snapshot()
        self.candidate = None
        self.changed_at = None
        self.next_check = 0.0
        self.state = "ready"

    def snapshot(self):
        paths = [self.root / "manage.py", self.root / "requirements.txt"]
        for directory in ("mainApp", "NovaSoft", "pos_shared", "local_pos"):
            paths.extend(path for path in (self.root / directory).rglob("*.py")
                         if not path.name.startswith("test")
                         and not {"__pycache__", "tests"}.intersection(path.parts))
        result = []
        for path in sorted(paths):
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            result.append((str(path.relative_to(self.root)), stat.st_size, stat.st_mtime_ns))
        return tuple(result)

    def poll(self):
        now = self.clock()
        if now < self.next_check:
            return self.state
        self.next_check = now + self.interval
        try:
            current = self.snapshot()
        except OSError:
            # A partially copied or temporarily inaccessible deploy is not ready.
            self.candidate, self.changed_at = None, None
            self.state = "waiting"
            return self.state
        if current == self.baseline:
            self.candidate, self.changed_at = None, None
            self.state = "ready"
        elif current != self.candidate:
            self.candidate, self.changed_at = current, now
            self.state = "waiting"
        elif now - self.changed_at >= self.settle:
            self.state = "reload"
        return self.state
