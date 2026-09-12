"""AC3: "An unactioned gate escalates to the configured backup approver
at exactly the SLA boundary, logged as an audit event."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gates.audit import AuditLog
from gates.escalation import AlreadyActionedError, EscalationTracker, GateSlaPolicy, UnknownGateError

FRIDAY = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
POLICY = GateSlaPolicy(business_days=2, backup_approver_id="acc-backup", backup_display_name="On-call Lead")


def test_gate_escalates_exactly_at_the_sla_boundary_and_logs_an_audit_event():
    audit = AuditLog()
    tracker = EscalationTracker(audit)
    tracker.open_gate(gate_id="g1", tenant_id="tenant-acme", run_id="run-1", gate_kind="plan_approval",
                       policy=POLICY, opened_at=FRIDAY)

    deadline = FRIDAY + timedelta(days=4)  # Friday + 2 business days = the following Tuesday
    just_before = deadline - timedelta(minutes=1)

    escalated = tracker.check_escalations(just_before)
    assert escalated == []
    assert tracker.is_escalated("g1") is False

    escalated = tracker.check_escalations(deadline)
    assert escalated == ["g1"]
    assert tracker.is_escalated("g1") is True

    events = [e for e in audit.all() if e.kind == "escalated"]
    assert len(events) == 1
    assert events[0].run_id == "run-1"
    assert events[0].details["backup_approver_id"] == "acc-backup"
    assert events[0].details["gate_id"] == "g1"


def test_gate_does_not_escalate_before_the_sla_boundary():
    audit = AuditLog()
    tracker = EscalationTracker(audit)
    tracker.open_gate(gate_id="g1", tenant_id="t", run_id="run-1", gate_kind="plan_approval",
                       policy=POLICY, opened_at=FRIDAY)
    tracker.check_escalations(FRIDAY + timedelta(hours=1))
    assert tracker.is_escalated("g1") is False
    assert [e for e in audit.all() if e.kind == "escalated"] == []


def test_actioned_gate_before_sla_never_escalates():
    audit = AuditLog()
    tracker = EscalationTracker(audit)
    tracker.open_gate(gate_id="g1", tenant_id="t", run_id="run-1", gate_kind="plan_approval",
                       policy=POLICY, opened_at=FRIDAY)
    tracker.mark_actioned("g1", actioned_at=FRIDAY + timedelta(hours=1))

    escalated = tracker.check_escalations(FRIDAY + timedelta(days=10))
    assert escalated == []
    with pytest.raises(AlreadyActionedError):
        tracker.mark_actioned("g1")


def test_risk_or_stuck_checkpoint_escalates_immediately_at_zero_sla():
    audit = AuditLog()
    tracker = EscalationTracker(audit)
    immediate_policy = GateSlaPolicy(business_days=0, backup_approver_id="acc-backup")
    tracker.open_gate(gate_id="g-checkpoint", tenant_id="t", run_id="run-9", gate_kind="risk_checkpoint",
                       policy=immediate_policy, opened_at=FRIDAY)

    escalated = tracker.check_escalations(FRIDAY)  # no elapsed time at all
    assert escalated == ["g-checkpoint"]


def test_current_authority_switches_to_backup_after_escalation():
    audit = AuditLog()
    tracker = EscalationTracker(audit)
    tracker.open_gate(gate_id="g1", tenant_id="t", run_id="run-1", gate_kind="plan_approval",
                       policy=POLICY, opened_at=FRIDAY)
    assert tracker.current_authority("g1", default_approver_id="acc-default") == "acc-default"

    tracker.check_escalations(FRIDAY + timedelta(days=10))
    assert tracker.current_authority("g1", default_approver_id="acc-default") == "acc-backup"


def test_escalation_is_idempotent_not_double_logged():
    audit = AuditLog()
    tracker = EscalationTracker(audit)
    tracker.open_gate(gate_id="g1", tenant_id="t", run_id="run-1", gate_kind="plan_approval",
                       policy=POLICY, opened_at=FRIDAY)
    later = FRIDAY + timedelta(days=10)
    tracker.check_escalations(later)
    tracker.check_escalations(later)
    tracker.check_escalations(later + timedelta(days=1))
    events = [e for e in audit.all() if e.kind == "escalated"]
    assert len(events) == 1


def test_unknown_gate_raises():
    tracker = EscalationTracker(AuditLog())
    with pytest.raises(UnknownGateError):
        tracker.mark_actioned("no-such-gate")
    with pytest.raises(UnknownGateError):
        tracker.is_escalated("no-such-gate")


def test_audit_log_is_durable_across_a_process_restart(tmp_path):
    audit1 = AuditLog(base_dir=tmp_path)
    tracker1 = EscalationTracker(audit1)
    tracker1.open_gate(gate_id="g1", tenant_id="t", run_id="run-1", gate_kind="plan_approval",
                        policy=POLICY, opened_at=FRIDAY)
    tracker1.check_escalations(FRIDAY + timedelta(days=10))

    # A fresh AuditLog instance pointed at the same directory (simulating
    # a process restart) sees the same durable events.
    audit2 = AuditLog(base_dir=tmp_path)
    kinds = [e.kind for e in audit2.all()]
    assert "gate_opened" in kinds
    assert "escalated" in kinds
