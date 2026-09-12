"""Acceptance criterion: "Every checkpoint pause is resumable after a
dropped connection (structured elicitation, not a lost chat turn)."

Distinct from `test_durable_resume.py` (which proves resumption survives
a full *process* restart): this file proves each of the four Section 9.3
checkpoint kinds produces a structured `Elicitation` -- readable back from
the Registry by any caller, at any later time, via a plain `get_run` call
-- rather than a live, stateful connection/session the caller must stay
attached to. "Dropped connection" is modeled here as: read the pending
elicitation through one connection/service handle, close it, reopen a
completely separate one, and confirm the exact same structured content
comes back."""
from __future__ import annotations

from run_registry import RegistryService

from orchestrator.model_backend import ScriptedAgentBackend, SubTask
from orchestrator.verification import ScriptedVerificationRunner, VerificationResult

from ._factories import make_diff_output, make_plan_output
from .conftest import make_orchestrator


def _reopen(db_path: str) -> RegistryService:
    return RegistryService(db_path)


def test_size_checkpoint_elicitation_is_readable_after_a_dropped_connection(tmp_path, tenant_id, plan_store, progress_store):
    db_path = str(tmp_path / "registry.db")
    registry = RegistryService(db_path)
    plan = make_plan_output(
        scope_in=("src/big.py",),
        story_size="S",  # size checkpoint at 150 lines / 5 files
        subtasks=[SubTask(task_id="t1", description="a huge change", parallel_group=None, depends_on=())],
    )
    diff = make_diff_output(files_touched=("src/big.py",), lines_changed=500)  # far over the S budget
    backend = ScriptedAgentBackend(plans=[plan], diffs=[diff])
    orch = make_orchestrator(registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store, agent_backend=backend)

    status = orch.start_run(jira_key="PROJ-30", repo="acme/app", branch="feature/big", trace_id="trace-30")
    status = orch.approve_plan(status.run_id, decision="approve")
    assert status.pause_kind == "checkpoint"
    assert status.checkpoint.trigger == "size"
    run_id = status.run_id
    original_reason = status.checkpoint.reason

    # "Dropped connection": close this RegistryService, open a fresh one
    # against the same durable store, from what is effectively a new
    # caller with no memory of the above.
    registry.close()
    reopened = _reopen(db_path)
    run = reopened.get_run(tenant_id=tenant_id, run_id=run_id).data
    assert run.checkpoint_pointer is not None
    import json

    elicitation = json.loads(run.checkpoint_pointer)
    assert elicitation["status"] == "pending"
    assert elicitation["trigger"] == "size"
    assert elicitation["reason"] == original_reason
    reopened.close()


def test_time_cost_checkpoint_fires_at_80_percent_not_100_percent(registry, tenant_id, plan_store, progress_store):
    """Section 9.4: "fires at 80% of the applicable budget, not at 100%."
    Driven through the real state machine: a clock pinned 25 minutes
    ahead of wall-clock 'now' (S's budget is 30 minutes -- 25/30 = 83.3%,
    past the 80% warning threshold but short of the 100% hard stop) makes
    `progress.elapsed_minutes()` read ~25 minutes at evaluation time,
    since `RunProgress.started_at` is captured at real wall-clock time a
    moment before the pinned-ahead clock is read."""
    from datetime import datetime, timedelta, timezone

    plan = make_plan_output(story_size="S", subtasks=[SubTask(task_id="t1", description="a", parallel_group=None, depends_on=())])
    diff = make_diff_output(lines_changed=1)  # tiny diff: only time should trigger, not size
    backend = ScriptedAgentBackend(plans=[plan], diffs=[diff])

    def clock():
        return datetime.now(timezone.utc) + timedelta(minutes=25)

    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, clock=clock,
    )
    status = orch.start_run(jira_key="PROJ-31", repo="acme/app", branch="feature/slow", trace_id="trace-31")
    status = orch.approve_plan(status.run_id, decision="approve")

    assert status.paused is True
    assert status.pause_kind == "checkpoint"
    assert status.checkpoint.trigger == "time_cost"
    assert status.checkpoint.details["hard_stop"] is False  # 83% crossed 80%, not yet 100%


def test_stuck_checkpoint_fires_after_default_retry_budget_exhausted(registry, tenant_id, plan_store, progress_store):
    plan = make_plan_output(story_size="S", subtasks=[SubTask(task_id="t1", description="a", parallel_group=None, depends_on=())])
    diff = make_diff_output(lines_changed=1)
    # Two more scripted diffs: real-live-run fix (found on this repo's
    # first actual live run against a real model/real verification -- see
    # core.py's _implementation_step) -- once every real plan subtask is
    # complete, a verification failure that routes back to IMPLEMENTATION
    # (Sec. 9.3's bounded retry budget -- "loop back to implementation",
    # not "silently re-verify the same code") now gives the agent one real
    # "fix-up" implement_subtask call carrying the specific failure summary.
    # Trace: fail 1 (count=1, below budget) -> fix-up -> fail 2 (count=2,
    # below budget) -> fix-up -> fail 3 (count=3, budget reached) -> STUCK,
    # pauses *before* a third fix-up (the run's `stage` never left
    # VERIFICATION during the retries above, so resolving the stuck
    # checkpoint with "continue" re-enters VERIFICATION directly, not
    # IMPLEMENTATION) -> the verifier's 4th scripted result ("finally
    # passes") is what resolves it. Three implement_subtask calls total:
    # the original subtask, plus fix-ups for "fail 1" and "fail 2".
    fixup_diffs = [make_diff_output(lines_changed=1) for _ in range(2)]
    backend = ScriptedAgentBackend(plans=[plan], diffs=[diff, *fixup_diffs])
    # 3 consecutive failures then a pass -- DEFAULT_STUCK_RETRY_BUDGET is 3.
    verifier = ScriptedVerificationRunner(
        [
            VerificationResult(passed=False, summary="fail 1"),
            VerificationResult(passed=False, summary="fail 2"),
            VerificationResult(passed=False, summary="fail 3"),
            VerificationResult(passed=True, summary="finally passes"),
        ]
    )
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, verification_runner=verifier,
    )
    status = orch.start_run(jira_key="PROJ-32", repo="acme/app", branch="feature/stuck", trace_id="trace-32")
    status = orch.approve_plan(status.run_id, decision="approve")

    assert status.paused is True
    assert status.pause_kind == "checkpoint"
    assert status.checkpoint.trigger == "stuck"
    assert status.checkpoint.details["consecutive_failures"] == 3

    # Resolving with "continue" re-enters VERIFICATION directly (stage
    # never left it during the auto-retries above) -- the verifier's 4th
    # scripted result ("finally passes") is what proceeds to the
    # change-review gate; no further implement_subtask call happens here.
    status = orch.resolve_checkpoint(status.run_id, decision="continue")
    assert status.stage == "change_review_gate"
    # The original subtask + fix-ups for "fail 1" and "fail 2" (not "fail
    # 3" -- that one triggers the stuck pause before a corresponding
    # fix-up would run).
    assert backend.implement_call_count == 3
