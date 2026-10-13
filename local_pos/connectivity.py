"""Cloud-first routing decisions shared by the integrated runserver runtime.

No writes, network calls or Django settings changes happen here. Health and
queue state must be obtained from the database/worker, never navigator.onLine.
A decision applies to a WHOLE request, not to individual failing SQL queries.
"""
from dataclasses import dataclass
from enum import Enum


class Health(str, Enum):
    AVAILABLE = "available"
    DISCONNECTED = "disconnected"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class Mode(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    SYNCHRONIZING = "synchronizing"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class LocalState:
    prepared: bool = False
    authorized: bool = False
    pending: int = 0
    conflicts: int = 0
    uncertain: int = 0
    # After the last acknowledgement, refresh the replica before returning to
    # cloud mode. Otherwise the next outage could use the pre-sync inventory.
    refresh_required: bool = False

    def __post_init__(self):
        for count in (self.pending, self.conflicts, self.uncertain):
            if type(count) is not int or count < 0:
                raise ValueError("Invalid queue state")


@dataclass(frozen=True)
class Decision:
    mode: Mode
    database: str | None
    new_operations: bool
    reason: str


def choose_mode(health: Health, local: LocalState) -> Decision:
    if not isinstance(health, Health):
        raise ValueError("Health must be an explicit probe result")
    if health == Health.REJECTED:
        return Decision(Mode.BLOCKED, None, False, "cloud_rejected")
    if local.conflicts:
        return Decision(Mode.BLOCKED, "offline" if local.prepared else None, False, "review_required")
    if health == Health.AVAILABLE:
        if local.pending or local.uncertain or local.refresh_required:
            return Decision(Mode.SYNCHRONIZING, "offline" if local.prepared else None,
                            local.prepared and local.authorized, "reconcile_before_cloud")
        return Decision(Mode.ONLINE, "default", True, "cloud_available")
    if health == Health.UNKNOWN:
        return Decision(Mode.UNAVAILABLE, None, False, "probe_required")
    if not local.prepared:
        return Decision(Mode.UNAVAILABLE, None, False, "local_not_prepared")
    if not local.authorized:
        return Decision(Mode.UNAVAILABLE, "offline", False, "local_authorization_expired")
    return Decision(Mode.OFFLINE, "offline", True, "cloud_unreachable")


def may_replay_locally(*, attempted_in_cloud: bool, confirmed_absent: bool) -> bool:
    """A timeout is NOT proof of rollback. An absent receipt must be verified."""
    return not attempted_in_cloud or confirmed_absent
