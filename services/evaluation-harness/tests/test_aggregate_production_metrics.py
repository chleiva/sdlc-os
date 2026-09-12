"""Integration test: `compute_production_metrics` assembles every
Section 20.2 live-production metric from one real, seeded tenant's worth
of `run_registry` data plus the pluggable cost/defect/gate-event sources.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from run_registry import stages

from evaluation_harness.gate_events import CHANGE_REVIEW, PLAN_APPROVAL, GateDecisionLog
from evaluation_harness.metrics.aggregate import compute_production_metrics
from evaluation_harness.metrics.cost import SyntheticAttemptCostSource
from evaluation_harness.metrics.defect_escape import SyntheticDefectEscapeSource

from .conftest import create_run, transition


def _complete(registry, tenant_id, clock, run):
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
        clock.advance(minutes=10)
        run = transition(registry, tenant_id, run, stage)
    return run


def test_full_production_metrics_report_from_real_registry_data(registry, tenant_id, clock):
    run_a = create_run(registry, tenant_id, jira_key="PROD-1", repo="org/repo")
    run_a = _complete(registry, tenant_id, clock, run_a)
    run_b = create_run(registry, tenant_id, jira_key="PROD-2", repo="org/repo")
    run_b = _complete(registry, tenant_id, clock, run_b)
    run_c = create_run(registry, tenant_id, jira_key="PROD-3", repo="org/repo")
    clock.advance(minutes=30)
    run_c = transition(registry, tenant_id, run_c, stages.RESEARCH)
    clock.advance(minutes=30)
    transition(registry, tenant_id, run_c, stages.ABANDONED)

    gate_log = GateDecisionLog()
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    gate_log.record(
        tenant_id=tenant_id, run_id=run_a.id, gate=PLAN_APPROVAL, decision="approved",
        opened_at=t0, decided_at=t0 + timedelta(hours=1), approver="alice",
    )
    gate_log.record(
        tenant_id=tenant_id, run_id=run_b.id, gate=CHANGE_REVIEW, decision="request_changes",
        opened_at=t0, decided_at=t0 + timedelta(hours=2), approver="bob",
    )

    report = compute_production_metrics(
        registry=registry,
        tenant_id=tenant_id,
        gate_log=gate_log,
        cost_source=SyntheticAttemptCostSource(),
        defect_source=SyntheticDefectEscapeSource({run_b.id}),
        human_baseline_hours=20.0,
        human_only_baseline_hours=6.0,
    )

    # Success rate: 2 completed, 1 abandoned, all classified short-task
    # here (single attempt, small elapsed time).
    assert report.success.overall_count == 3
    assert report.success.short_task_count == 3
    assert report.success.long_horizon_count == 0
    assert report.success.long_horizon_success_rate is None  # no long-horizon data this period

    # Rework: neither PROD-1 nor PROD-2 were reopened.
    assert report.rework.rework_count == 0

    # Cost: 3 runs' worth of attempts, all metered.
    assert report.cost.completed_task_count == 2
    assert report.cost.total_cost > 0

    # Defect escape: run_b flagged, 1 of 2 completed runs.
    assert report.defect_escape.completed_count == 2
    assert report.defect_escape.escaped_count == 1
    assert report.defect_escape.defect_escape_rate == 0.5

    # Overrides: plan-approval 0% override, change-review 100% override.
    assert report.overrides.by_gate[PLAN_APPROVAL].override_rate == 0.0
    assert report.overrides.by_gate[CHANGE_REVIEW].override_rate == 1.0

    # Net human time: explicit baseline flows through untouched.
    assert report.net_human_time.human_only_baseline_hours == 6.0
    assert report.net_human_time.system_assisted_human_hours_mean == 1.5  # mean of 1h and 2h
    assert report.net_human_time.net_human_time_hours == pytest.approx(4.5)

    # Cycle time: explicit baseline flows through untouched.
    assert report.cycle_time.human_baseline_hours == 20.0
    assert report.cycle_time.mean_system_hours is not None


def test_compute_production_metrics_requires_both_explicit_baselines(registry, tenant_id):
    gate_log = GateDecisionLog()
    with pytest.raises(TypeError):
        compute_production_metrics(
            registry=registry,
            tenant_id=tenant_id,
            gate_log=gate_log,
            cost_source=SyntheticAttemptCostSource(),
            defect_source=SyntheticDefectEscapeSource(),
            human_only_baseline_hours=6.0,
            # human_baseline_hours omitted -> TypeError, never a silent zero
        )
