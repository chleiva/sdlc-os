"""`orchestrator.model_backend`'s `AgentBackend` interface -- specifically
the default `implement_subtasks_parallel` (see its own docstring): the
exact old sequential behavior, so every backend that doesn't override it
(everything except `BedrockToolUseAgentBackend` -- see
`test_tool_use_bedrock_backend.py::TestImplementSubtasksParallel` for the
real-concurrency override) keeps working unchanged."""

from __future__ import annotations

from orchestrator.model_backend import ScriptedAgentBackend, SubTask

from ._factories import make_diff_output


def test_default_implement_subtasks_parallel_calls_implement_subtask_once_each_in_order():
    diffs = [
        make_diff_output(files_touched=("a.py",), lines_changed=1, subtask_id="t1"),
        make_diff_output(files_touched=("b.py",), lines_changed=2, subtask_id="t2"),
    ]
    backend = ScriptedAgentBackend(diffs=diffs)

    result = backend.implement_subtasks_parallel(
        run_context={"run_id": "r1"},
        subtasks=[
            SubTask(task_id="t1", description="a", parallel_group="g1", depends_on=()),
            SubTask(task_id="t2", description="b", parallel_group="g1", depends_on=()),
        ],
    )

    assert [d.subtask_id for d in result] == ["t1", "t2"]
    assert backend.implement_call_count == 2  # real sequential fallback, not a fake batch call
