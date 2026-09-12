"""Gate-decision event log: the one piece of state this package tracks
that is genuinely NOT reconstructable from a fresh read of F2's public
`RegistryService` API.

Why this exists (read before treating it as a "parallel data store" --
it deliberately is not one): `RegistryService` exposes `Run` (current
state) and an append-only `Attempt` list (attempt boundaries: who started
it, why, when it ended). It does **not** expose per-stage-transition
timestamps or which concrete human decision (approve / request-changes /
reject) produced a given transition -- that finer-grained
`StageHistoryEntry` data exists in F2's own store (see
`run_registry.repository.list_stage_history`) but is not part of
`RegistryService`'s public contract, and D13's brief scopes this
deliverable to consuming F2's Registry Service only (not its internal
repository module, which `tests/test_no_direct_db_access.py` in
run-registry's own suite structurally forbids reaching for anyway).

So: gate decisions and gate wait-time are new information this module
defines a minimal event shape for, meant to be emitted by whichever real
component actually calls `RegistryService.transition_stage` /
`append_attempt` to enact a gate decision (D9's gate service, in a real
deployment) -- at the same call site, the same moment, using the same
real timestamps. In this package's own tests, the test seeding plays
that role directly: every `GateDecisionEvent` recorded is paired with a
real `RegistryService.transition_stage`/`append_attempt` call it
describes, not invented independently of one.

ASSUMPTION FLAGGED FOR HUMAN REVIEW: a future F2 revision could fold this
directly into the Registry Service's own schema (e.g. exposing
`list_stage_history` publicly, or recording decision/approver on the
stage-history row itself) rather than leaving D13 to define a companion
event shape. That would remove this module entirely in favor of reading
gate history straight out of `RegistryService`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from evaluation_harness.timeutil import hours_between, to_iso

# Fixed vocabulary, mirrors the gates named in Section 12 / Section 12.1.
PLAN_APPROVAL = "plan_approval"
CHANGE_REVIEW = "change_review"
CHECKPOINT = "checkpoint"

APPROVED = "approved"
REQUEST_CHANGES = "request_changes"
REJECTED = "rejected"

_DECISIONS = frozenset({APPROVED, REQUEST_CHANGES, REJECTED})
_GATES = frozenset({PLAN_APPROVAL, CHANGE_REVIEW, CHECKPOINT})


@dataclass(frozen=True)
class GateDecisionEvent:
    tenant_id: str
    run_id: str
    gate: str
    decision: str
    opened_at: str  # ISO -- when the gate fired / entered the human's queue
    decided_at: str  # ISO -- when the human actually acted
    approver: str

    def __post_init__(self) -> None:
        if self.gate not in _GATES:
            raise ValueError(f"unknown gate {self.gate!r}, must be one of {sorted(_GATES)}")
        if self.decision not in _DECISIONS:
            raise ValueError(
                f"unknown decision {self.decision!r}, must be one of {sorted(_DECISIONS)}"
            )

    @property
    def wait_hours(self) -> float:
        return hours_between(self.opened_at, self.decided_at)


class GateDecisionLog:
    """Tenant-scoped, in-memory append-only log of `GateDecisionEvent`s."""

    def __init__(self) -> None:
        self._events: list[GateDecisionEvent] = []

    def record(
        self,
        *,
        tenant_id: str,
        run_id: str,
        gate: str,
        decision: str,
        opened_at: datetime | str,
        decided_at: datetime | str,
        approver: str,
    ) -> GateDecisionEvent:
        event = GateDecisionEvent(
            tenant_id=tenant_id,
            run_id=run_id,
            gate=gate,
            decision=decision,
            opened_at=opened_at if isinstance(opened_at, str) else to_iso(opened_at),
            decided_at=decided_at if isinstance(decided_at, str) else to_iso(decided_at),
            approver=approver,
        )
        self._events.append(event)
        return event

    def for_tenant(self, tenant_id: str) -> list[GateDecisionEvent]:
        return [e for e in self._events if e.tenant_id == tenant_id]

    def for_gate(self, tenant_id: str, gate: str) -> list[GateDecisionEvent]:
        return [e for e in self.for_tenant(tenant_id) if e.gate == gate]

    def all_events(self) -> list[GateDecisionEvent]:
        return list(self._events)
