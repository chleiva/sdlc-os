"""Rework rate, cost per completed task (including discarded/retried
work), and defect escape rate -- Section 20.2 bullets 3, 5, 6.
"""

from __future__ import annotations

from run_registry import stages

from evaluation_harness.metrics.cost import SyntheticAttemptCostSource, compute_cost_per_completed_task
from evaluation_harness.metrics.defect_escape import (
    SyntheticDefectEscapeSource,
    compute_defect_escape_rate,
)
from evaluation_harness.metrics.rework import compute_rework_rate

from .conftest import create_run, new_attempt, transition


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


# ---------------------------------------------------------------------------
# Rework rate
# ---------------------------------------------------------------------------


def test_reopened_ticket_within_window_counts_as_rework(registry, tenant_id, clock):
    original = create_run(registry, tenant_id, jira_key="PROJ-500", repo="org/repo")
    original = _complete(registry, tenant_id, clock, original)

    # Reopened 2 days later for a follow-up fix -- well inside a 14-day window.
    clock.advance(days=2)
    create_run(registry, tenant_id, jira_key="PROJ-500", repo="org/repo")

    report = compute_rework_rate(registry=registry, tenant_id=tenant_id, window_hours=24 * 14)
    assert report.completed_count == 1
    assert report.rework_count == 1
    assert report.rework_rate == 1.0
    assert original.id in report.rework_run_ids


def test_reopened_ticket_outside_window_does_not_count_as_rework(registry, tenant_id, clock):
    original = create_run(registry, tenant_id, jira_key="PROJ-501", repo="org/repo")
    original = _complete(registry, tenant_id, clock, original)

    # Reopened 30 days later -- outside a 14-day window.
    clock.advance(days=30)
    create_run(registry, tenant_id, jira_key="PROJ-501", repo="org/repo")

    report = compute_rework_rate(registry=registry, tenant_id=tenant_id, window_hours=24 * 14)
    assert report.rework_count == 0
    assert report.rework_rate == 0.0


def test_a_ticket_never_reopened_is_not_rework(registry, tenant_id, clock):
    run = create_run(registry, tenant_id, jira_key="PROJ-502", repo="org/repo")
    _complete(registry, tenant_id, clock, run)

    report = compute_rework_rate(registry=registry, tenant_id=tenant_id)
    assert report.completed_count == 1
    assert report.rework_count == 0


# ---------------------------------------------------------------------------
# Cost per completed task (including discarded/retried work)
# ---------------------------------------------------------------------------


def test_cost_per_completed_task_rises_with_retries_and_abandoned_waste(registry, tenant_id, clock):
    cost_source = SyntheticAttemptCostSource(base_cost=1.0, hourly_rate=2.0, retry_multiplier=1.5)

    # Baseline: one clean run, no retries.
    clean_run = create_run(registry, tenant_id)
    _complete(registry, tenant_id, clock, clean_run)
    report_clean = compute_cost_per_completed_task(
        registry=registry, tenant_id=tenant_id, cost_source=cost_source
    )
    clean_cost_per_task = report_clean.cost_per_completed_task
    assert report_clean.completed_task_count == 1
    assert report_clean.discarded_cost == 0.0

    # A second run that needed two retries before finishing, plus one run
    # that was abandoned outright (pure waste, never completes).
    retried_run = create_run(registry, tenant_id)
    for stage in (stages.RESEARCH, stages.PLAN_AUTHORING, stages.PLAN_APPROVAL_GATE, stages.IMPLEMENTATION):
        clock.advance(minutes=15)
        retried_run = transition(registry, tenant_id, retried_run, stage)
    for _ in range(2):
        clock.advance(hours=1)
        retried_run, _ = new_attempt(
            registry, tenant_id, retried_run, reason="retry", starting_stage=stages.IMPLEMENTATION
        )
    for stage in (
        stages.VERIFICATION,
        stages.CHANGE_REVIEW_GATE,
        stages.PACKAGING,
        stages.RETROSPECTIVE,
        stages.COMPLETED,
    ):
        clock.advance(minutes=15)
        retried_run = transition(registry, tenant_id, retried_run, stage)

    abandoned_run = create_run(registry, tenant_id)
    clock.advance(minutes=30)
    transition(registry, tenant_id, abandoned_run, stages.RESEARCH)

    report_after = compute_cost_per_completed_task(
        registry=registry, tenant_id=tenant_id, cost_source=cost_source
    )
    assert report_after.completed_task_count == 2  # clean_run + retried_run
    assert report_after.discarded_cost > 0.0  # the 2 retry attempts + the abandoned run's attempt
    # Cost per completed task now reflects real system-wide waste, not just
    # the two clean completions -- it is materially higher than the
    # clean-only baseline.
    assert report_after.cost_per_completed_task > clean_cost_per_task


def test_no_completed_tasks_reports_none_cost_per_task(registry, tenant_id):
    cost_source = SyntheticAttemptCostSource()
    report = compute_cost_per_completed_task(
        registry=registry, tenant_id=tenant_id, cost_source=cost_source
    )
    assert report.completed_task_count == 0
    assert report.cost_per_completed_task is None


# ---------------------------------------------------------------------------
# Defect escape rate
# ---------------------------------------------------------------------------


def test_defect_escape_rate_from_pluggable_source(registry, tenant_id, clock):
    run_a = create_run(registry, tenant_id)
    run_a = _complete(registry, tenant_id, clock, run_a)
    run_b = create_run(registry, tenant_id)
    run_b = _complete(registry, tenant_id, clock, run_b)
    run_c = create_run(registry, tenant_id)
    run_c = _complete(registry, tenant_id, clock, run_c)

    defect_source = SyntheticDefectEscapeSource({run_b.id})
    report = compute_defect_escape_rate(
        registry=registry, tenant_id=tenant_id, defect_source=defect_source
    )
    assert report.completed_count == 3
    assert report.escaped_count == 1
    assert round(report.defect_escape_rate, 4) == round(1 / 3, 4)
