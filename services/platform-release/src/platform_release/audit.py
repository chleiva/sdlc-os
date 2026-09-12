"""A durable audit log for platform release/rollback/module-versioning
events (master spec §16.3, referred to directly by §14.15: "[a rollback]
is itself logged as a Section 16.3 audit event, the same discipline
applied to a model-version rollback (Section 13.5)").

This is the platform-release domain's own instance of the same
append-only, JSON-lines-backed audit pattern already established twice
in this codebase -- `services/source-control/src/source_control/audit.py`
(bot-identity/GitHub-call audit trail) and
`services/gates/src/gates/audit.py` (gate-decision audit trail). Per this
deliverable's own instructions, this is a fresh instance of that pattern
for a different domain (platform releases/rollbacks/module promotions),
not an import of either -- the three audit domains are independent and
never share a store.

Same flagged assumption `gates/audit.py` already flags for its own
domain: F2's Run Registry schema has no columns for "platform release N
was rolled out / rolled back at time T" -- that is not Run-scoped data
at all (a release spans every tenant's runs, not one), so it has no
natural home in F2's schema. This module owns its own durable store
instead, the same choice `gates/audit.py` made for gate events. A human
should confirm this is the intended home for platform-release audit
events (vs., say, a dedicated audit-events table in some future
platform-wide schema) -- not a decision this deliverable is authorized
to make unilaterally.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# The fixed vocabulary of platform-release audit event kinds. Every
# `AuditLog.record` call must use one of these -- see `test_audit.py`'s
# structural test asserting no caller in this package ever constructs an
# event kind outside this set (same discipline
# `source_control/audit.py`'s own `_BOT_ACTOR_RE` structural check
# applies to actor shape).
EVENT_KINDS = frozenset(
    {
        "release_started",
        "release_ci_report",
        "canary_routing_configured",
        "release_promoted_full",
        "rollback_started",
        "rollback_completed",
        "module_version_validated",
        "module_version_promoted",
        "module_version_rejected",
        "cell_definition_rejected",
        "cell_definition_accepted",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    ts: str
    kind: str
    actor: str
    details: dict

    @staticmethod
    def new(*, kind: str, actor: str, details: dict, now: datetime | None = None) -> "AuditEvent":
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown platform-release audit event kind: {kind!r} (expected one of {sorted(EVENT_KINDS)})")
        ts = (now or datetime.now(timezone.utc)).isoformat()
        return AuditEvent(event_id=str(uuid4()), ts=ts, kind=kind, actor=actor, details=details)


class AuditLog:
    """Append-only, thread-safe audit trail for platform release
    engineering events. Backed by a JSON-lines file when `base_dir` is
    given (durable across a process restart, matching every other
    per-domain store in this repo), or purely in-memory otherwise (handy
    for a unit test that doesn't care about durability)."""

    _FILENAME = "platform_release_audit_log.jsonl"

    def __init__(self, base_dir: str | Path | None = None):
        self._lock = threading.Lock()
        self._events: list[AuditEvent] = []
        self._path: Path | None = None
        if base_dir is not None:
            base = Path(base_dir)
            base.mkdir(parents=True, exist_ok=True)
            self._path = base / self._FILENAME
            if self._path.exists():
                for line in self._path.read_text().splitlines():
                    if line.strip():
                        self._events.append(AuditEvent(**json.loads(line)))

    def record(self, *, kind: str, actor: str, details: dict, now: datetime | None = None) -> AuditEvent:
        event = AuditEvent.new(kind=kind, actor=actor, details=details, now=now)
        with self._lock:
            self._events.append(event)
            if self._path is not None:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(event)) + "\n")
        return event

    def all(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)

    def of_kind(self, kind: str) -> list[AuditEvent]:
        with self._lock:
            return [e for e in self._events if e.kind == kind]
