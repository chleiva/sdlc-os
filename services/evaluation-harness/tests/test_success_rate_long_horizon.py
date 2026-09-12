"""Task success rate, with long-horizon tracked separately from
short-task success (Section 20.2 bullets 1 and 7; acceptance criterion:
"Long-horizon and short-task success rates are reported as genuinely
separate numbers, never blended into one headline figure.").

Every Run/Attempt below is seeded through REAL `RegistryService` calls
(`create_run`, `transition_stage`, `append_attempt`) -- `clock` (see
conftest.py) only controls what timestamp those real calls are stamped
with, so the resulting Attempt history is real data, not a hand-built
dict standing in for one.
"""

from __future__ import annotations

from run_registry import stages

from .conftest import create_run, new_attempt, transition


def _run_short_success(registry, tenant_id, clock):
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
        clock.advance(minutes=10)
        run = transition(registry, tenant_id, run, stage)
    return run


def _run_short_failure(registry, tenant_id, clock):
    run = create_run(registry, tenant_id)
    clock.advance(minutes=15)
    run = transition(registry, tenant_id, run, stages.RESEARCH)
    clock.advance(minutes=15)
    run = transition(registry, tenant_id, run, stages.ABANDONED)
    return run


def _run_long_horizon_success(registry, tenant_id, clock, *, extra_attempts=2):
    run = create_run(registry, tenant_id)
    for stage in (stages.RESEARCH, stages.PLAN_AUTHORING, stages.PLAN_APPROVAL_GATE, stages.IMPLEMENTATION):
        clock.advance(minutes=20)
        run = transition(registry, tenant_id, run, stage)
    for _ in range(extra_attempts):
        clock.advance(hours=2)
        run, _ = new_attempt(
            registry, tenant_id, run, reason="retry", starting_stage=stages.IMPLEMENTATION
        )
    for stage in (
        stages.VERIFICATION,
        stages.CHANGE_REVIEW_GATE,
        stages.PACKAGING,
        stages.RETROSPECTIVE,
        stages.COMPLETED,
    ):
        clock.advance(minutes=20)
        run = transition(registry, tenant_id, run, stage)
    return run


def _run_long_horizon_failure(registry, tenant_id, clock, *, extra_attempts=2):
    run = create_run(registry, tenant_id)
    clock.advance(minutes=20)
    run = transition(registry, tenant_id, run, stages.RESEARCH)
    clock.advance(minutes=20)
    run = transition(registry, tenant_id, run, stages.PLAN_AUTHORING)
    for _ in range(extra_attempts):
        clock.advance(hours=2)
        run, _ = new_attempt(
            registry, tenant_id, run, reason="retry", starting_stage=stages.PLAN_AUTHORING
        )
    clock.advance(hours=1)
    run = transition(registry, tenant_id, run, stages.ABANDONED)
    return run


def test_short_and_long_horizon_success_rates_are_separate_and_collapse_matches_section_11_2(
    registry, tenant_id, clock
):
    """Mirrors Section 11.2's finding directly: short-task success is
    high, long-horizon success collapses -- and the two numbers must
    both be visible, not averaged away."""
    from evaluation_harness.metrics.success import compute_task_success_rate

    # 3 short successes, 1 short failure -> short success rate 0.75
    for _ in range(3):
        _run_short_success(registry, tenant_id, clock)
    _run_short_failure(registry, tenant_id, clock)

    # 1 long-horizon success, 3 long-horizon failures -> long-horizon rate 0.25
    _run_long_horizon_success(registry, tenant_id, clock)
    for _ in range(3):
        _run_long_horizon_failure(registry, tenant_id, clock)

    report = compute_task_success_rate(registry=registry, tenant_id=tenant_id)

    assert report.short_task_count == 4
    assert report.long_horizon_count == 4
    assert report.short_task_success_rate == 0.75
    assert report.long_horizon_success_rate == 0.25

    # Genuinely separate fields -- neither equals the overall figure, and
    # the overall figure alone would hide the collapse.
    assert report.short_task_success_rate != report.long_horizon_success_rate
    assert report.overall_success_rate == 0.5
    assert report.overall_success_rate != report.short_task_success_rate
    assert report.overall_success_rate != report.long_horizon_success_rate


def test_long_horizon_classification_triggers_on_attempt_count_alone(registry, tenant_id, clock):
    from evaluation_harness.task_classification import LONG_HORIZON, classify_horizon

    run = create_run(registry, tenant_id)
    clock.advance(minutes=5)
    run = transition(registry, tenant_id, run, stages.RESEARCH)
    # Three total attempts (initial + 2 retries), all within a short
    # wall-clock span -- attempt count alone crosses the threshold.
    run, _ = new_attempt(registry, tenant_id, run, reason="retry", starting_stage=stages.RESEARCH)
    clock.advance(seconds=30)
    run, _ = new_attempt(registry, tenant_id, run, reason="retry", starting_stage=stages.RESEARCH)

    attempts = registry.list_attempts(tenant_id=tenant_id, run_id=run.id).data
    assert classify_horizon(attempts) == LONG_HORIZON


def test_short_task_with_no_data_reports_none_not_zero(registry, tenant_id):
    from evaluation_harness.metrics.success import compute_task_success_rate

    report = compute_task_success_rate(registry=registry, tenant_id=tenant_id)
    assert report.short_task_success_rate is None
    assert report.long_horizon_success_rate is None
    assert report.overall_success_rate == 0.0
