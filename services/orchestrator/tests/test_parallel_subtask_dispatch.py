"""Real batching: `core.py`'s `_implementation_step` groups same-`parallel_group`,
dependency-satisfied subtasks and dispatches the whole batch through
`AgentBackend.implement_subtasks_parallel` in one call, instead of one
subtask at a time -- closing the gap `worktree.py`'s own docstring flagged
("`SubTask.parallel_group` existed, but nothing called more than one
subtask at a time"). These tests exercise the *dispatch* decision only
(which method gets called, with which subtasks, in which order) against a
recording `AgentBackend` double -- `test_tool_use_bedrock_backend.py`
separately covers the real concurrent git-worktree execution underneath
`BedrockToolUseAgentBackend`'s own override.
"""

from __future__ import annotations

from orchestrator.model_backend import ScriptedAgentBackend, SubTask

from ._factories import make_diff_output, make_plan_output
from .conftest import make_orchestrator


class _RecordingAgentBackend(ScriptedAgentBackend):
    """Records every `implement_subtask`/`implement_subtasks_parallel`
    call it receives (which method, which subtask ids, in which order)
    instead of consuming a fixed scripted list -- keyed by task_id so
    this test can assert dispatch shape independent of call order."""

    def __init__(self, *, plans, diffs_by_task_id: dict) -> None:
        super().__init__(plans=plans, diffs=[])
        self._diffs_by_task_id = dict(diffs_by_task_id)
        self.sequential_calls: list[str] = []
        self.parallel_batches: list[list[str]] = []

    def implement_subtask(self, *, run_context, subtask):
        self.implement_call_count += 1
        self.sequential_calls.append(subtask.task_id)
        return self._diffs_by_task_id[subtask.task_id]

    def implement_subtasks_parallel(self, *, run_context, subtasks):
        self.parallel_batches.append([s.task_id for s in subtasks])
        # Deliberately does NOT call self.implement_subtask -- that would
        # also record into sequential_calls, conflating the two dispatch
        # paths this test needs to tell apart.
        return [self._diffs_by_task_id[s.task_id] for s in subtasks]


def test_two_ready_subtasks_sharing_a_parallel_group_dispatch_as_one_batch(registry, tenant_id, tmp_path):
    from orchestrator.plan_artifact import PlanArtifactStore
    from orchestrator.progress import RunProgressStore

    plan = make_plan_output(
        scope_in=("src/a.py", "src/b.py"),
        subtasks=[
            SubTask(task_id="t1", description="implement a", parallel_group="g1", depends_on=(), interface_contract="a() -> None"),
            SubTask(task_id="t2", description="implement b", parallel_group="g1", depends_on=(), interface_contract="b() -> None"),
        ],
    )
    backend = _RecordingAgentBackend(
        plans=[plan],
        diffs_by_task_id={
            "t1": make_diff_output(files_touched=("src/a.py",), lines_changed=5, subtask_id="t1"),
            "t2": make_diff_output(files_touched=("src/b.py",), lines_changed=7, subtask_id="t2"),
        },
    )
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, agent_backend=backend,
        plan_store=PlanArtifactStore(tmp_path / "plans"), progress_store=RunProgressStore(tmp_path / "progress"),
    )
    status = orch.start_run(jira_key="PROJ-1", repo="acme/app", branch="feature/x", trace_id="trace-1")
    status = orch.approve_plan(status.run_id, decision="approve")

    assert backend.parallel_batches == [["t1", "t2"]]  # one real batched call, not two sequential ones
    assert backend.sequential_calls == []  # implement_subtask only reached via the batch's own default fallback
    assert status.stage == "change_review_gate"
    assert status.paused is True

    progress = RunProgressStore(tmp_path / "progress").load(status.run_id)
    assert set(progress.completed_subtask_ids) == {"t1", "t2"}
    assert set(progress.files_touched) == {"src/a.py", "src/b.py"}
    assert progress.lines_changed == 12


def test_a_lone_subtask_with_no_parallel_group_still_dispatches_sequentially(registry, tenant_id, tmp_path):
    from orchestrator.plan_artifact import PlanArtifactStore
    from orchestrator.progress import RunProgressStore

    plan = make_plan_output(
        subtasks=[SubTask(task_id="t1", description="implement a", parallel_group=None, depends_on=())],
    )
    backend = _RecordingAgentBackend(
        plans=[plan], diffs_by_task_id={"t1": make_diff_output(subtask_id="t1")},
    )
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, agent_backend=backend,
        plan_store=PlanArtifactStore(tmp_path / "plans"), progress_store=RunProgressStore(tmp_path / "progress"),
    )
    status = orch.start_run(jira_key="PROJ-2", repo="acme/app", branch="feature/y", trace_id="trace-2")
    orch.approve_plan(status.run_id, decision="approve")

    assert backend.sequential_calls == ["t1"]  # the exact, unchanged pre-parallelism path
    assert backend.parallel_batches == []


def test_a_groupmate_not_yet_ready_is_never_batched_with_its_own_dependency(registry, tenant_id, tmp_path):
    """t2 shares t1's `parallel_group` but depends on t1 itself -- it is
    never independently ready at the same moment t1 is, so the two must
    never appear together in one `implement_subtasks_parallel` batch.
    Real bug this guards: batching by group membership alone (ignoring
    per-subtask readiness) would hand a subtask to a concurrent worker
    before the real dependency output (from a *sibling* worker) it
    needs even exists."""
    from orchestrator.plan_artifact import PlanArtifactStore
    from orchestrator.progress import RunProgressStore

    plan = make_plan_output(
        scope_in=("src/a.py", "src/b.py"),
        subtasks=[
            SubTask(task_id="t1", description="implement a", parallel_group="g1", depends_on=(), interface_contract="a() -> None"),
            SubTask(task_id="t2", description="implement b, needs a", parallel_group="g1", depends_on=("t1",), interface_contract="b() -> None"),
        ],
    )
    backend = _RecordingAgentBackend(
        plans=[plan],
        diffs_by_task_id={
            "t1": make_diff_output(files_touched=("src/a.py",), lines_changed=2, subtask_id="t1"),
            "t2": make_diff_output(files_touched=("src/b.py",), lines_changed=3, subtask_id="t2"),
        },
    )
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, agent_backend=backend,
        plan_store=PlanArtifactStore(tmp_path / "plans"), progress_store=RunProgressStore(tmp_path / "progress"),
    )
    status = orch.start_run(jira_key="PROJ-3", repo="acme/app", branch="feature/z", trace_id="trace-3")
    orch.approve_plan(status.run_id, decision="approve")

    # Real dependency ordering preserved: t1 then t2, both dispatched
    # alone (batch size 1 each round) since t2 is never ready while t1
    # is still in flight -- never batched together.
    assert backend.sequential_calls == ["t1", "t2"]
    assert backend.parallel_batches == []
