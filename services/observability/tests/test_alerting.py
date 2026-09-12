"""Acceptance criterion (from the task brief, sharpening master spec
Section 16.4's "alerting, not babysitting"): "a stuck-checkpoint event
and a budget-threshold-crossed event both reach [the alert sink], not
just the dashboard."

First a direct unit-level proof of `AlertSink`/`FileAlertSink` itself,
then an end-to-end proof driving the real `Orchestrator` through an
actual "stuck" checkpoint AND an actual "time_cost" (budget-threshold)
checkpoint in the same step, confirming both alerts reach the sink even
though only one of them becomes the pausing Elicitation the human sees.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from run_registry import RegistryService

from observability import FileAlertSink, InMemoryAlertSink, ObservabilityClient
from observability.alerting import make_alert
from orchestrator.checkpoints import DEFAULT_BUDGETS
from orchestrator.core import Orchestrator
from orchestrator.model_backend import AcceptanceCriterion, DiffOutput, PlanOutput, ScriptedAgentBackend, SubTask
from orchestrator.plan_artifact import PlanArtifactStore
from orchestrator.progress import RunProgress, RunProgressStore
from orchestrator.verification import ScriptedVerificationRunner, VerificationResult


def test_in_memory_alert_sink_receives_pushed_alerts():
    alert_sink = InMemoryAlertSink()
    alert_sink.push(make_alert(kind="checkpoint_stuck", message="stuck", run_id="r1"))
    alert_sink.push(make_alert(kind="checkpoint_time_cost", message="budget", run_id="r1"))
    assert [a.kind for a in alert_sink.alerts] == ["checkpoint_stuck", "checkpoint_time_cost"]


def test_file_alert_sink_is_durable_and_readable_back(tmp_path):
    path = tmp_path / "alerts.jsonl"
    alert_sink = FileAlertSink(path)
    alert_sink.push(make_alert(kind="checkpoint_stuck", message="3 consecutive failures", run_id="r1"))
    alert_sink.push(make_alert(kind="checkpoint_time_cost", message="80% of budget", run_id="r1"))
    alert_sink.close()

    recovered = FileAlertSink.read_all(path)
    assert [a.kind for a in recovered] == ["checkpoint_stuck", "checkpoint_time_cost"]


def _one_subtask_high_risk_plan() -> PlanOutput:
    return PlanOutput(
        outcomes="A change worth watching closely.",
        acceptance_criteria=(
            AcceptanceCriterion(criterion_id="AC1", description="x", verification_tests=("t",)),
        ),
        scope_in=("src/a.py",),
        scope_out=(),
        subtasks=(SubTask(task_id="t1", description="implement a", parallel_group=None, depends_on=()),),
        story_size="S",
        cross_cutting_or_high_risk=False,
        risk_tier="low",
        rollback_strategy="git revert",
    )


def test_stuck_and_budget_threshold_alerts_both_reach_the_alert_sink(tmp_path, tenant_id, sink, alert_sink):
    """Drives the real Orchestrator's `_verification_step` into a state
    where BOTH the "stuck" trigger (3 consecutive same-stage verification
    failures, Section 9.3) and the "time_cost" trigger (80% of the S
    budget's wall-clock/cost ceiling, Section 9.4) are true at once --
    proving both alerts reach the sink from the same step, even though
    only "stuck" becomes the pausing Elicitation (see
    `Orchestrator._verification_step`)."""
    registry = RegistryService(str(tmp_path / "registry.db"))
    observability = ObservabilityClient(sink=sink, service_name="orchestrator", alert_sink=alert_sink)

    plan_store = PlanArtifactStore(tmp_path / "plan_artifacts")
    progress_store = RunProgressStore(tmp_path / "progress")

    # Verification always fails -- drives consecutive_same_stage_failures
    # up to the DEFAULT_STUCK_RETRY_BUDGET (3).
    failing_verifier = ScriptedVerificationRunner(
        [VerificationResult(passed=False, summary="failed") for _ in range(5)]
    )

    # A clock that reports far enough in the future to cross the S
    # budget's 80% wall-clock warning threshold (30min * 0.8 = 24min),
    # regardless of when `progress.started_at` was actually recorded.
    budget = DEFAULT_BUDGETS["S"]
    future_time = datetime.now(timezone.utc) + timedelta(minutes=budget.wall_clock_minutes)

    orch = Orchestrator(
        registry=registry,
        tenant_id=tenant_id,
        agent_backend=ScriptedAgentBackend(plans=[_one_subtask_high_risk_plan()]),
        verification_runner=failing_verifier,
        plan_store=plan_store,
        progress_store=progress_store,
        observability=observability,
        clock=lambda: future_time,
    )
    status = orch.start_run(jira_key="PROJ-400", repo="acme/app", branch="feature/alerts", trace_id="trace-alerts")
    run_id = status.run_id

    # Drive straight to the verification stage without going through
    # `implement_subtask` (which would need a scripted diff) -- write the
    # subtask "already completed" directly into the durable progress
    # store (a real, on-disk RunProgress, same store the orchestrator
    # itself reads/writes), then transition the run to `verification` and
    # call `approve_plan`'s equivalent path via repeated `_verification_step`
    # runs through the public `resolve_checkpoint`/drive loop.
    progress = RunProgress(run_id=run_id, attempt_id="", completed_subtask_ids=["t1"], files_touched=["src/a.py"], lines_changed=10)
    progress_store.save(progress)
    run = registry.get_run(tenant_id=tenant_id, run_id=run_id).data
    registry.transition_stage(tenant_id=tenant_id, run_id=run_id, expected_version=run.version, next_stage="implementation")
    run = registry.get_run(tenant_id=tenant_id, run_id=run_id).data
    registry.transition_stage(tenant_id=tenant_id, run_id=run_id, expected_version=run.version, next_stage="verification")

    # First failing verification: consecutive_same_stage_failures -> 1,2,3
    # across repeated calls until "stuck" fires. Call the orchestrator's
    # own resume path each time (mirrors _drive's own loop shape) so this
    # goes through the real, unmodified `_verification_step` each time.
    for _ in range(3):
        run = registry.get_run(tenant_id=tenant_id, run_id=run_id).data
        if run.stage != "verification":
            break
        orch.resume_run(run_id)

    stuck_alerts = [a for a in alert_sink.alerts if a.kind == "checkpoint_stuck"]
    time_cost_alerts = [a for a in alert_sink.alerts if a.kind == "checkpoint_time_cost"]
    assert stuck_alerts, "expected at least one checkpoint_stuck alert to reach the AlertSink"
    assert time_cost_alerts, "expected at least one checkpoint_time_cost (budget-threshold) alert to reach the AlertSink"
    for a in stuck_alerts + time_cost_alerts:
        assert a.run_id == run_id
        assert a.trace_id == "trace-alerts"

    registry.close()
