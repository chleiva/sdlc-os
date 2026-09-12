"""A durable audit log for gate events (master spec Sec. 16.3, referred
to by Sec. 12.1: "the escalation itself is logged as a Section 16.3
audit event, not a silent reassignment").

ASSUMPTION FLAGGED FOR HUMAN REVIEW (same discipline as orchestrator/
progress.py's own flagged assumption): F2's Run Registry schema has no
audit-event table/columns (its `RegistryService` public API has no
`list_stage_history`/audit method at all -- see repository.py's
internal, unexposed `list_stage_history`), so gate audit events live in
their own durable store this deliverable owns, exactly the pattern
`orchestrator.progress.RunProgressStore` already established for
implementation-progress data F2 also has no columns for. A human should
confirm whether gate audit events belong in F2's schema instead (a
schema-owning decision this deliverable is not authorized to make
unilaterally).
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    ts: str
    tenant_id: str
    run_id: str
    kind: str  # "gate_opened" | "gate_cleared" | "gate_rejected" | "escalated" | "batch_closed" | "batch_story_pulled"
    details: dict

    @staticmethod
    def new(*, tenant_id: str, run_id: str, kind: str, details: dict, now: datetime | None = None) -> "AuditEvent":
        ts = (now or datetime.now(timezone.utc)).isoformat()
        return AuditEvent(event_id=str(uuid4()), ts=ts, tenant_id=tenant_id, run_id=run_id, kind=kind, details=details)


class AuditLog:
    """Append-only, thread-safe audit trail. Backed by a JSON-lines
    file when `base_dir` is given (durable across a process restart,
    like every other per-run store in this repo), or purely in-memory
    otherwise (handy for a unit test that does not care about
    durability)."""

    def __init__(self, base_dir: str | Path | None = None):
        self._lock = threading.Lock()
        self._events: list[AuditEvent] = []
        self._path: Path | None = None
        if base_dir is not None:
            base = Path(base_dir)
            base.mkdir(parents=True, exist_ok=True)
            self._path = base / "gate_audit_log.jsonl"
            if self._path.exists():
                for line in self._path.read_text().splitlines():
                    if line.strip():
                        self._events.append(AuditEvent(**json.loads(line)))

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)
            if self._path is not None:
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(asdict(event)) + "\n")

    def all(self) -> list[AuditEvent]:
        with self._lock:
            return list(self._events)

    def for_run(self, run_id: str) -> list[AuditEvent]:
        with self._lock:
            return [e for e in self._events if e.run_id == run_id]
