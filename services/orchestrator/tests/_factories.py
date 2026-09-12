"""Shared test-data builders -- not a test module itself (no test_ prefix)."""
from __future__ import annotations

from orchestrator.model_backend import AcceptanceCriterion, DiffOutput, PlanOutput, SubTask


def make_plan_output(
    *,
    scope_in=("src/foo.py", "tests/test_foo.py"),
    scope_out=("src/bar.py",),
    subtasks=None,
    story_size="S",
    cross_cutting_or_high_risk=False,
    risk_tier="low",
) -> PlanOutput:
    if subtasks is None:
        subtasks = (
            SubTask(task_id="t1", description="implement foo", parallel_group=None, depends_on=()),
        )
    return PlanOutput(
        outcomes="Add foo to the service.",
        acceptance_criteria=(
            AcceptanceCriterion(criterion_id="AC1", description="foo works", verification_tests=("tests/test_foo.py::test_foo",)),
        ),
        scope_in=scope_in,
        scope_out=scope_out,
        subtasks=tuple(subtasks),
        story_size=story_size,
        cross_cutting_or_high_risk=cross_cutting_or_high_risk,
        risk_tier=risk_tier,
        rollback_strategy="git revert the merge commit",
    )


def make_diff_output(*, files_touched=("src/foo.py",), lines_changed=10, subtask_id="t1") -> DiffOutput:
    return DiffOutput(files_touched=files_touched, lines_changed=lines_changed, commit_message=f"implement {subtask_id}", subtask_id=subtask_id)
