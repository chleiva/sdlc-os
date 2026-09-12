"""Escalation on approver unavailability (master spec Sec. 12.1): "if
the default approver has not acted within a configured SLA (default:
two business days) at a plan-approval or change-review gate, or
immediately at a Section 9.3 risk/stuck checkpoint, the request routes
to a configured backup approver ..., and the escalation itself is
logged as a Section 16.3 audit event, not a silent reassignment."

This module owns the durable bookkeeping of "when did this gate open,
and has it since been actioned" (see audit.py's module docstring for
why this lives here rather than in F2's Run Registry), plus the actual
escalation decision, expressed as a small state machine per open gate:

    OPEN --(actor acts before SLA)--> ACTIONED
    OPEN --(SLA boundary reached, unactioned)--> ESCALATED --(backup acts)--> ACTIONED
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from gates.audit import AuditEvent, AuditLog
from gates.business_calendar import is_sla_breached


class UnknownGateError(Exception):
    pass


class AlreadyActionedError(Exception):
    pass


@dataclass(frozen=True)
class GateSlaPolicy:
    business_days: float
    backup_approver_id: str
    backup_display_name: str | None = None


@dataclass
class _OpenGate:
    gate_id: str
    tenant_id: str
    run_id: str
    gate_kind: str
    opened_at: datetime
    policy: GateSlaPolicy
    escalated: bool = False
    actioned: bool = False


class EscalationTracker:
    """Tracks every currently-open gate this deliverable knows about
    and escalates the ones that cross their SLA boundary unactioned.
    `check_escalations(now)` is the periodic sweep a caller (a
    scheduler, or a test driving a fake clock) invokes; nothing in this
    class spawns its own background thread, keeping it fully
    deterministic under test."""

    def __init__(self, audit_log: AuditLog, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self._audit = audit_log
        self._clock = clock
        self._lock = threading.Lock()
        self._gates: dict[str, _OpenGate] = {}

    def open_gate(
        self, *, gate_id: str, tenant_id: str, run_id: str, gate_kind: str, policy: GateSlaPolicy,
        opened_at: datetime | None = None,
    ) -> None:
        opened = opened_at or self._clock()
        with self._lock:
            self._gates[gate_id] = _OpenGate(
                gate_id=gate_id, tenant_id=tenant_id, run_id=run_id, gate_kind=gate_kind,
                opened_at=opened, policy=policy,
            )
        self._audit.record(AuditEvent.new(
            tenant_id=tenant_id, run_id=run_id, kind="gate_opened",
            details={"gate_id": gate_id, "gate_kind": gate_kind, "opened_at": opened.isoformat(),
                      "sla_business_days": policy.business_days},
            now=opened,
        ))

    def mark_actioned(self, gate_id: str, *, actioned_at: datetime | None = None) -> None:
        with self._lock:
            gate = self._gates.get(gate_id)
            if gate is None:
                raise UnknownGateError(gate_id)
            if gate.actioned:
                raise AlreadyActionedError(gate_id)
            gate.actioned = True
            escalated = gate.escalated
            tenant_id, run_id, gate_kind = gate.tenant_id, gate.run_id, gate.gate_kind
        self._audit.record(AuditEvent.new(
            tenant_id=tenant_id, run_id=run_id, kind="gate_actioned",
            details={"gate_id": gate_id, "gate_kind": gate_kind, "was_escalated": escalated},
            now=actioned_at or self._clock(),
        ))

    def is_escalated(self, gate_id: str) -> bool:
        with self._lock:
            gate = self._gates.get(gate_id)
            if gate is None:
                raise UnknownGateError(gate_id)
            return gate.escalated

    def current_authority(self, gate_id: str, *, default_approver_id: str) -> str:
        """The identity currently entitled to act on this gate: the
        default approver, unless escalation has already fired, in
        which case it is the configured backup approver."""
        with self._lock:
            gate = self._gates.get(gate_id)
            if gate is None:
                raise UnknownGateError(gate_id)
            return gate.policy.backup_approver_id if gate.escalated else default_approver_id

    def check_escalations(self, now: datetime | None = None) -> list[str]:
        """Sweeps every open, unactioned gate and escalates any that
        have crossed their SLA boundary. Returns the gate_ids that were
        escalated by this call (empty if none). Idempotent: a gate
        already escalated is not re-escalated or re-logged."""
        current = now or self._clock()
        newly_escalated: list[str] = []
        with self._lock:
            candidates = [g for g in self._gates.values() if not g.actioned and not g.escalated]
        for gate in candidates:
            if is_sla_breached(gate.opened_at, current, business_days=gate.policy.business_days):
                with self._lock:
                    if gate.actioned or gate.escalated:
                        continue
                    gate.escalated = True
                self._audit.record(AuditEvent.new(
                    tenant_id=gate.tenant_id, run_id=gate.run_id, kind="escalated",
                    details={
                        "gate_id": gate.gate_id,
                        "gate_kind": gate.gate_kind,
                        "opened_at": gate.opened_at.isoformat(),
                        "sla_business_days": gate.policy.business_days,
                        "escalated_at": current.isoformat(),
                        "backup_approver_id": gate.policy.backup_approver_id,
                    },
                    now=current,
                ))
                newly_escalated.append(gate.gate_id)
        return newly_escalated
