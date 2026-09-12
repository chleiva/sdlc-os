"""Human override/rejection rate at each gate (Section 20.2 bullet 2,
Section 12.1) -- "a rising rate is a leading indicator of a problem
before success rate itself drops."

Computed from `gate_events.GateDecisionLog` -- see that module's
docstring for why gate-decision outcomes are tracked there rather than
reconstructed from `RegistryService` alone.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluation_harness.gate_events import (
    APPROVED,
    CHANGE_REVIEW,
    CHECKPOINT,
    PLAN_APPROVAL,
    REJECTED,
    REQUEST_CHANGES,
    GateDecisionLog,
)

ALL_GATES = (PLAN_APPROVAL, CHANGE_REVIEW, CHECKPOINT)


@dataclass(frozen=True)
class GateOverrideStats:
    gate: str
    total_decisions: int
    approved: int
    request_changes: int
    rejected: int
    override_rate: float  # (request_changes + rejected) / total_decisions


@dataclass(frozen=True)
class OverrideRateReport:
    tenant_id: str
    by_gate: dict[str, GateOverrideStats]


def compute_override_rates(*, gate_log: GateDecisionLog, tenant_id: str) -> OverrideRateReport:
    by_gate: dict[str, GateOverrideStats] = {}
    for gate in ALL_GATES:
        events = gate_log.for_gate(tenant_id, gate)
        total = len(events)
        approved = sum(1 for e in events if e.decision == APPROVED)
        request_changes = sum(1 for e in events if e.decision == REQUEST_CHANGES)
        rejected = sum(1 for e in events if e.decision == REJECTED)
        override_rate = ((request_changes + rejected) / total) if total else 0.0
        by_gate[gate] = GateOverrideStats(
            gate=gate,
            total_decisions=total,
            approved=approved,
            request_changes=request_changes,
            rejected=rejected,
            override_rate=override_rate,
        )
    return OverrideRateReport(tenant_id=tenant_id, by_gate=by_gate)
