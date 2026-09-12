"""`InternalHistoricalTicketsSuite` (Section 20.1 bullet 5): "An internal
set modeled on the organization's own historical tickets." Per the
brief, its task set is seeded from real synthetic `run_registry` data
created here through the real `RegistryService`, not invented in the
suite module itself.
"""

from __future__ import annotations

from run_registry import stages

from evaluation_harness.benchmarks import InternalHistoricalTicketsSuite, build_tasks_from_registry
from evaluation_harness.runner import BenchmarkRunner
from evaluation_harness.scheduling import DueScheduler, InMemoryLastRunStore

from .conftest import create_run, new_attempt, transition


def _complete_cleanly(registry, tenant_id, clock, run):
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


def _abandon(registry, tenant_id, clock, run):
    clock.advance(minutes=10)
    run = transition(registry, tenant_id, run, stages.RESEARCH)
    clock.advance(minutes=10)
    return transition(registry, tenant_id, run, stages.ABANDONED)


def test_task_set_is_built_from_real_registry_runs_not_invented(registry, tenant_id, clock):
    completed = create_run(registry, tenant_id, jira_key="HIST-1", repo="org/repo")
    completed = _complete_cleanly(registry, tenant_id, clock, completed)

    abandoned = create_run(registry, tenant_id, jira_key="HIST-2", repo="org/repo")
    _abandon(registry, tenant_id, clock, abandoned)

    tasks = build_tasks_from_registry(registry, tenant_id=tenant_id)
    task_ids = {t.task_id for t in tasks}
    assert task_ids == {completed.id, abandoned.id}

    completed_task = next(t for t in tasks if t.task_id == completed.id)
    assert completed_task.metadata["jira_key"] == "HIST-1"
    assert completed_task.metadata["original_outcome"] == "completed"

    abandoned_task = next(t for t in tasks if t.task_id == abandoned.id)
    assert abandoned_task.metadata["original_outcome"] == "abandoned"


def test_suite_regrades_historical_tickets_and_aggregates_through_the_real_runner(
    registry, tenant_id, clock
):
    # A ticket that completed cleanly in one attempt -> should re-pass.
    clean = create_run(registry, tenant_id, jira_key="HIST-10", repo="org/repo")
    clean = _complete_cleanly(registry, tenant_id, clock, clean)

    # A ticket that needed heavy retries before completing -> flagged fragile.
    fragile = create_run(registry, tenant_id, jira_key="HIST-11", repo="org/repo")
    for stage in (stages.RESEARCH, stages.PLAN_AUTHORING, stages.PLAN_APPROVAL_GATE, stages.IMPLEMENTATION):
        clock.advance(minutes=10)
        fragile = transition(registry, tenant_id, fragile, stage)
    for _ in range(3):
        clock.advance(hours=1)
        fragile, _ = new_attempt(
            registry, tenant_id, fragile, reason="retry", starting_stage=stages.IMPLEMENTATION
        )
    for stage in (
        stages.VERIFICATION,
        stages.CHANGE_REVIEW_GATE,
        stages.PACKAGING,
        stages.RETROSPECTIVE,
        stages.COMPLETED,
    ):
        clock.advance(minutes=10)
        fragile = transition(registry, tenant_id, fragile, stage)

    # A ticket that was abandoned outright.
    abandoned = create_run(registry, tenant_id, jira_key="HIST-12", repo="org/repo")
    _abandon(registry, tenant_id, clock, abandoned)

    tasks = build_tasks_from_registry(registry, tenant_id=tenant_id)
    suite = InternalHistoricalTicketsSuite(tasks, max_acceptable_attempts=2)

    scheduler = DueScheduler(store=InMemoryLastRunStore())
    runner = BenchmarkRunner(scheduler)
    runner.register(suite)
    outcome = runner.run_suite("internal-historical-tickets", force=True)

    assert outcome.ran is True
    results_by_task = {r.task_id: r for r in outcome.result.results}
    assert results_by_task[clean.id].passed is True
    assert results_by_task[fragile.id].passed is False  # too many attempts -> fragile, not a clean pass
    assert results_by_task[abandoned.id].passed is False  # never completed
    assert outcome.result.total == 3
    assert 0 < outcome.result.passed_count < outcome.result.total
