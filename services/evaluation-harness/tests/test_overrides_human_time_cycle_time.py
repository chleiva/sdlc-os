"""Human override/rejection rate per gate (Section 20.2 bullet 2), net
human time vs. a stated human-only baseline (Section 20.2 bullet 8,
Section 23.5), and cycle time vs. a stated human baseline (Section 20.2
bullet 4).

Acceptance criterion under test: "Net-human-time is computed against a
stated human-only baseline, not asserted without one." Both
`compute_net_human_time` and `compute_cycle_time` require their baseline
as a mandatory keyword argument -- omitting it is a `TypeError`, and
passing `None` explicitly is a `ValueError`, so a caller cannot silently
default it to zero either way.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from run_registry import stages

from evaluation_harness.gate_events import CHANGE_REVIEW, PLAN_APPROVAL, GateDecisionLog
from evaluation_harness.metrics.cycle_time import compute_cycle_time
from evaluation_harness.metrics.human_time import compute_net_human_time
from evaluation_harness.metrics.overrides import compute_override_rates

from .conftest import create_run, transition


# ---------------------------------------------------------------------------
# Human override/rejection rate at each gate
# ---------------------------------------------------------------------------


def test_override_rate_computed_per_gate_independently(registry, tenant_id):
    log = GateDecisionLog()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    # Plan-approval gate: 3 approved, 1 request-changes, 0 rejected -> 25% override.
    for i in range(3):
        log.record(
            tenant_id=tenant_id, run_id=f"run-{i}", gate=PLAN_APPROVAL, decision="approved",
            opened_at=t0, decided_at=t0 + timedelta(hours=1), approver="alice",
        )
    log.record(
        tenant_id=tenant_id, run_id="run-3", gate=PLAN_APPROVAL, decision="request_changes",
        opened_at=t0, decided_at=t0 + timedelta(hours=3), approver="alice",
    )

    # Change-review gate: 1 approved, 1 rejected -> 50% override.
    log.record(
        tenant_id=tenant_id, run_id="run-4", gate=CHANGE_REVIEW, decision="approved",
        opened_at=t0, decided_at=t0 + timedelta(hours=1), approver="bob",
    )
    log.record(
        tenant_id=tenant_id, run_id="run-5", gate=CHANGE_REVIEW, decision="rejected",
        opened_at=t0, decided_at=t0 + timedelta(hours=1), approver="bob",
    )

    report = compute_override_rates(gate_log=log, tenant_id=tenant_id)
    assert report.by_gate[PLAN_APPROVAL].total_decisions == 4
    assert report.by_gate[PLAN_APPROVAL].override_rate == 0.25
    assert report.by_gate[CHANGE_REVIEW].total_decisions == 2
    assert report.by_gate[CHANGE_REVIEW].override_rate == 0.5
    # A gate with zero decisions reports 0.0, not an error or NaN.
    assert report.by_gate["checkpoint"].total_decisions == 0
    assert report.by_gate["checkpoint"].override_rate == 0.0


def test_gate_events_are_scoped_per_tenant(registry, tenant_id):
    log = GateDecisionLog()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    log.record(
        tenant_id=tenant_id, run_id="run-a", gate=PLAN_APPROVAL, decision="approved",
        opened_at=t0, decided_at=t0, approver="alice",
    )
    log.record(
        tenant_id="a-different-tenant", run_id="run-b", gate=PLAN_APPROVAL, decision="rejected",
        opened_at=t0, decided_at=t0, approver="carol",
    )
    report = compute_override_rates(gate_log=log, tenant_id=tenant_id)
    assert report.by_gate[PLAN_APPROVAL].total_decisions == 1
    assert report.by_gate[PLAN_APPROVAL].override_rate == 0.0


# ---------------------------------------------------------------------------
# Net human time vs. a stated human-only baseline
# ---------------------------------------------------------------------------


def test_net_human_time_requires_an_explicit_baseline_argument():
    log = GateDecisionLog()
    with pytest.raises(TypeError):
        compute_net_human_time(gate_log=log, tenant_id="t1")  # missing required baseline


def test_net_human_time_rejects_none_baseline_explicitly():
    log = GateDecisionLog()
    with pytest.raises(ValueError):
        compute_net_human_time(gate_log=log, tenant_id="t1", human_only_baseline_hours=None)


def test_net_human_time_is_computed_against_the_stated_baseline(tenant_id):
    log = GateDecisionLog()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Two runs, each with 1 hour of real gate-decision wait time.
    log.record(
        tenant_id=tenant_id, run_id="run-1", gate=PLAN_APPROVAL, decision="approved",
        opened_at=t0, decided_at=t0 + timedelta(hours=1), approver="alice",
    )
    log.record(
        tenant_id=tenant_id, run_id="run-2", gate=PLAN_APPROVAL, decision="approved",
        opened_at=t0, decided_at=t0 + timedelta(hours=1), approver="alice",
    )

    report_high_baseline = compute_net_human_time(
        gate_log=log, tenant_id=tenant_id, human_only_baseline_hours=8.0
    )
    report_low_baseline = compute_net_human_time(
        gate_log=log, tenant_id=tenant_id, human_only_baseline_hours=0.5
    )

    # Same underlying data, different stated baseline -> different net
    # figure. The baseline is a real, load-bearing input, not a constant.
    assert report_high_baseline.human_only_baseline_hours == 8.0
    assert report_low_baseline.human_only_baseline_hours == 0.5
    assert report_high_baseline.net_human_time_hours != report_low_baseline.net_human_time_hours
    assert report_high_baseline.system_assisted_human_hours_mean == 1.0
    assert report_high_baseline.net_human_time_hours == 7.0
    # A lower baseline than the System's own assisted time correctly
    # reports a NEGATIVE net time -- the System took longer than the
    # (small) baseline, the exact felt-faster/actually-slower gap
    # Section 23.5 exists to catch, never silently clamped to zero.
    assert report_low_baseline.net_human_time_hours == pytest.approx(-0.5)


def test_additional_human_hours_not_captured_by_gate_events_is_explicit_and_additive(tenant_id):
    log = GateDecisionLog()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    log.record(
        tenant_id=tenant_id, run_id="run-1", gate=PLAN_APPROVAL, decision="approved",
        opened_at=t0, decided_at=t0 + timedelta(hours=1), approver="alice",
    )
    without_extra = compute_net_human_time(
        gate_log=log, tenant_id=tenant_id, human_only_baseline_hours=8.0
    )
    with_extra = compute_net_human_time(
        gate_log=log,
        tenant_id=tenant_id,
        human_only_baseline_hours=8.0,
        additional_human_hours_per_task=2.0,
    )
    assert with_extra.system_assisted_human_hours_mean == without_extra.system_assisted_human_hours_mean + 2.0
    assert with_extra.net_human_time_hours < without_extra.net_human_time_hours


# ---------------------------------------------------------------------------
# Cycle time vs. a stated human baseline
# ---------------------------------------------------------------------------


def test_cycle_time_requires_an_explicit_baseline_argument(registry, tenant_id):
    with pytest.raises(TypeError):
        compute_cycle_time(registry=registry, tenant_id=tenant_id)  # missing required baseline


def test_cycle_time_rejects_none_baseline_explicitly(registry, tenant_id):
    with pytest.raises(ValueError):
        compute_cycle_time(registry=registry, tenant_id=tenant_id, human_baseline_hours=None)


def test_cycle_time_delta_reflects_the_stated_baseline(registry, tenant_id, clock):
    run = create_run(registry, tenant_id)
    for stage in (
        stages.RESEARCH,
        stages.PLAN_AUTHORING,
        stages.PLAN_APPROVAL_GATE,
        stages.IMPLEMENTATION,
        stages.VERIFICATION,
        stages.CHANGE_REVIEW_GATE,
        stages.PACKAGING,
        stages.RETROSPECTIVE,
        stages.COMPLETED,
    ):
        clock.advance(hours=1)
        run = transition(registry, tenant_id, run, stage)

    report_fast_baseline = compute_cycle_time(
        registry=registry, tenant_id=tenant_id, human_baseline_hours=4.0
    )
    report_slow_baseline = compute_cycle_time(
        registry=registry, tenant_id=tenant_id, human_baseline_hours=40.0
    )
    assert report_fast_baseline.mean_system_hours == 9.0  # 9 one-hour transitions
    assert report_fast_baseline.delta_vs_baseline_hours == pytest.approx(5.0)
    assert report_slow_baseline.delta_vs_baseline_hours == pytest.approx(-31.0)
