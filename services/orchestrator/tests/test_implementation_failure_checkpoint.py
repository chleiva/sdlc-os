"""`Orchestrator._handle_implementation_failure` -- the real live-run gap
this closes: an exception raised by `AgentBackend.implement_subtask`/
`implement_subtasks_parallel` (e.g. a real
`tool_use_bedrock_backend.BedrockAgenticLoopExhaustedError`, or any other
real implementation-stage failure) used to propagate all the way out of
`Orchestrator._drive` uncaught, leaving `deploy/run-worker/jira_poll_run
.py`'s top-level "a real, transient infrastructure failure must never
crash this whole process" handler as the only thing that ever saw it --
indistinguishable there from a genuine transient blip, and never counted
toward Section 9.3's real "stuck" checkpoint
(`RunProgress.consecutive_same_stage_failures` was only ever incremented
by verification failures). A systematically failing subtask therefore
retried silently and identically forever, once per external poll tick,
with no human ever seeing it.

These tests exercise the fix directly against a real `Orchestrator`, a
real `RegistryService`, and a real, durable `RunProgressStore` -- only
the `AgentBackend`/`VerificationRunner` are test doubles (this package's
own established "real code, mocked external boundary" discipline)."""
from __future__ import annotations

from orchestrator.model_backend import AgentBackend, DiffOutput, PlanOutput, ScriptedAgentBackend, SubTask
from orchestrator.verification import ScriptedVerificationRunner, VerificationResult

from ._factories import make_diff_output, make_plan_output
from .conftest import make_orchestrator


class _FlakyThenSucceedsBackend(AgentBackend):
    """A real `AgentBackend` implementation (not a mock of one) that
    raises a real `RuntimeError` on `implement_subtask` the first
    `fail_times` calls, then returns `diff` on every call after that --
    `ScriptedAgentBackend` has no way to script "fail, then succeed" for
    the same subtask, so this stands in for a real backend that is
    genuinely, transiently unreliable (a real Bedrock throttle that
    eventually clears, for instance) rather than structurally stuck."""

    def __init__(self, *, plan: PlanOutput, diff: DiffOutput, fail_times: int) -> None:
        self._plan = plan
        self._diff = diff
        self._fail_times = fail_times
        self.implement_call_count = 0

    def author_plan(self, *, run_context: dict) -> PlanOutput:
        return self._plan

    def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
        return self._plan

    def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
        self.implement_call_count += 1
        if self.implement_call_count <= self._fail_times:
            raise RuntimeError(f"simulated transient implementation failure #{self.implement_call_count}")
        return self._diff


def test_a_single_implementation_failure_retries_in_process_without_pausing(registry, tenant_id, plan_store, progress_store):
    """Below the stuck retry budget (default 3): the same still-incomplete
    subtask is retried immediately, in-process -- no checkpoint, no wait
    for an external poll tick, exactly like a "retry" verification
    outcome already behaves."""
    plan = make_plan_output(subtasks=[SubTask(task_id="t1", description="flaky", parallel_group=None, depends_on=())])
    diff = make_diff_output(files_touched=("src/foo.py",), lines_changed=10, subtask_id="t1")
    backend = _FlakyThenSucceedsBackend(plan=plan, diff=diff, fail_times=2)
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, verification_runner=ScriptedVerificationRunner([VerificationResult(passed=True, summary="ok")]),
    )

    status = orch.start_run(jira_key="PROJ-40", repo="acme/app", branch="feature/flaky", trace_id="trace-40")
    status = orch.approve_plan(status.run_id, decision="approve")

    # Two real failures, then a real success -- all inside this one
    # approve_plan call, then straight through to verification passing
    # and the real change-review gate, never pausing on a checkpoint.
    assert backend.implement_call_count == 3
    assert status.paused is True
    assert status.pause_kind == "gate"
    assert status.stage == "change_review_gate"

    progress = progress_store.load(status.run_id)
    assert progress.completed_subtask_ids == ["t1"]
    # t1's own eventual real success resets the counter -- a failure
    # that was ultimately overcome must not keep counting toward
    # "stuck" for whatever comes after it.
    assert progress.consecutive_same_stage_failures == 0


def test_repeated_implementation_failures_pause_at_a_real_stuck_checkpoint(registry, tenant_id, plan_store, progress_store):
    """At the stuck retry budget (default 3 consecutive failures): a real,
    human-visible "stuck" checkpoint pauses the run -- never an unbounded
    silent retry loop, and never an uncaught exception crashing the whole
    process."""
    plan = make_plan_output(subtasks=[SubTask(task_id="t1", description="never works", parallel_group=None, depends_on=())])
    diff = make_diff_output(files_touched=("src/foo.py",), lines_changed=10, subtask_id="t1")
    backend = _FlakyThenSucceedsBackend(plan=plan, diff=diff, fail_times=99)  # never actually succeeds
    orch = make_orchestrator(registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store, agent_backend=backend)

    status = orch.start_run(jira_key="PROJ-41", repo="acme/app", branch="feature/stuck", trace_id="trace-41")
    status = orch.approve_plan(status.run_id, decision="approve")

    assert backend.implement_call_count == 3  # DEFAULT_STUCK_RETRY_BUDGET
    assert status.paused is True
    assert status.pause_kind == "checkpoint"
    assert status.checkpoint.trigger == "stuck"
    assert "3" in status.checkpoint.reason  # names the real retry budget it exceeded

    progress = progress_store.load(status.run_id)
    assert progress.completed_subtask_ids == []  # never completed -- every attempt genuinely failed
    assert progress.consecutive_same_stage_failures == 3

    # Resolving with "continue" retries the identical subtask again --
    # this time against a backend that (in a real scenario) may finally
    # succeed. Confirms the run isn't left permanently wedged.
    backend._fail_times = 0  # simulate the underlying real problem being fixed
    resumed = orch.resolve_checkpoint(status.run_id, decision="continue")
    assert resumed.paused is True
    assert resumed.pause_kind == "gate"
    progress = progress_store.load(status.run_id)
    assert progress.completed_subtask_ids == ["t1"]
    assert progress.consecutive_same_stage_failures == 0


def test_a_failure_on_a_later_subtask_does_not_inherit_an_earlier_ones_failure_count(registry, tenant_id, plan_store, progress_store):
    """The counter must reflect CONSECUTIVE failures on the current
    problem, not a cumulative tally across unrelated subtasks -- t1
    fails twice then succeeds (resetting the counter), so t2 failing
    once afterward must not be treated as "3 in a row" and pause."""
    plan = make_plan_output(
        scope_in=("src/t1.py", "src/t2.py"),  # must cover both real files touched below, or the real "risk" checkpoint fires instead
        subtasks=[
            SubTask(task_id="t1", description="flaky", parallel_group=None, depends_on=()),
            SubTask(task_id="t2", description="also flaky", parallel_group=None, depends_on=("t1",)),
        ]
    )

    class _TwoSubtaskFlakyBackend(AgentBackend):
        def __init__(self) -> None:
            self.implement_call_count = 0

        def author_plan(self, *, run_context: dict) -> PlanOutput:
            return plan

        def re_plan(self, *, run_context: dict, feedback: str) -> PlanOutput:
            return plan

        def implement_subtask(self, *, run_context: dict, subtask: SubTask) -> DiffOutput:
            self.implement_call_count += 1
            if subtask.task_id == "t1" and self.implement_call_count <= 2:
                raise RuntimeError("t1 transiently failing")
            if subtask.task_id == "t2" and self.implement_call_count == 3:
                raise RuntimeError("t2's first attempt fails")
            return make_diff_output(files_touched=(f"src/{subtask.task_id}.py",), lines_changed=5, subtask_id=subtask.task_id)

    backend = _TwoSubtaskFlakyBackend()
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, verification_runner=ScriptedVerificationRunner([VerificationResult(passed=True, summary="ok")]),
    )

    status = orch.start_run(jira_key="PROJ-42", repo="acme/app", branch="feature/two-flaky", trace_id="trace-42")
    status = orch.approve_plan(status.run_id, decision="approve")

    # t1: fail, fail, succeed (resets counter to 0); t2: fail, succeed
    # (only 1 consecutive failure at the time it succeeds) -- never
    # reaches the retry budget of 3, so no checkpoint.
    assert status.paused is True
    assert status.pause_kind == "gate"
    progress = progress_store.load(status.run_id)
    assert set(progress.completed_subtask_ids) == {"t1", "t2"}
    assert progress.consecutive_same_stage_failures == 0
