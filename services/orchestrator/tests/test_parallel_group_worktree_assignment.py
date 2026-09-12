"""Section 8.1's "contracts before parallel writes" + worktree isolation,
wired together: a plan artifact whose sub-task graph marks two subtasks
as a parallel group gets one real, isolated, non-overlapping worktree per
subtask -- proving the plan artifact's parallel/sequential markers
actually drive real worktree assignment, not just sit in a document."""
from __future__ import annotations

import pytest

from orchestrator.checkpoints import DEFAULT_BUDGETS
from orchestrator.model_backend import SubTask
from orchestrator.plan_artifact import generate_plan_artifact
from orchestrator.worktree import WorktreeError, assign_worktrees_for_parallel_group

from ._factories import make_plan_output


def test_parallel_group_subtasks_each_get_a_distinct_worktree(git_fixture_repo, tmp_path):
    plan = make_plan_output(
        subtasks=[
            SubTask(task_id="t1", description="a", parallel_group="g1", depends_on=(), interface_contract="shape A"),
            SubTask(task_id="t2", description="b", parallel_group="g1", depends_on=(), interface_contract="shape B"),
            SubTask(task_id="t3", description="c", parallel_group=None, depends_on=("t1", "t2")),
        ]
    )
    artifact = generate_plan_artifact(run_id="run-parallel-1", plan_version=1, plan_output=plan, budget=DEFAULT_BUDGETS["S"], human_plan_text="t")

    handles = assign_worktrees_for_parallel_group(
        artifact=artifact, group_name="g1", repo_path=git_fixture_repo, base_ref="main", worktrees_root=tmp_path / "worktrees"
    )

    assert set(handles) == {"t1", "t2"}
    assert handles["t1"].worktree_path != handles["t2"].worktree_path
    from pathlib import Path

    assert Path(handles["t1"].worktree_path).is_dir()
    assert Path(handles["t2"].worktree_path).is_dir()


def test_assigning_worktrees_for_a_non_parallel_group_is_rejected(git_fixture_repo, tmp_path):
    plan = make_plan_output(subtasks=[SubTask(task_id="t1", description="a", parallel_group=None, depends_on=())])
    artifact = generate_plan_artifact(run_id="run-parallel-2", plan_version=1, plan_output=plan, budget=DEFAULT_BUDGETS["S"], human_plan_text="t")
    with pytest.raises(WorktreeError):
        assign_worktrees_for_parallel_group(
            artifact=artifact, group_name="does-not-exist", repo_path=git_fixture_repo, base_ref="main", worktrees_root=tmp_path / "worktrees"
        )
