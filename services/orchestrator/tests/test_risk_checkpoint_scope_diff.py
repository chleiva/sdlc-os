"""Acceptance criterion: "The structured plan artifact's declared-scope
field is what the Section 9.3 risk checkpoint diffs against -- verified
with a test case where the diff touches a file outside declared scope
and the run pauses."
"""
from __future__ import annotations

from orchestrator.model_backend import ScriptedAgentBackend, SubTask
from orchestrator.verification import ScriptedVerificationRunner, VerificationResult

from ._factories import make_diff_output, make_plan_output
from .conftest import make_orchestrator


def test_diff_touching_out_of_scope_file_pauses_the_run(registry, tenant_id, plan_store, progress_store):
    plan = make_plan_output(
        scope_in=("src/foo.py",),
        scope_out=("src/secrets.py",),
        subtasks=[SubTask(task_id="t1", description="implement foo", parallel_group=None, depends_on=())],
    )
    # The scripted diff touches a file that is neither declared in-scope
    # nor out-of-scope -- exactly Section 9.3's "touches a file/module
    # outside the plan's declared scope" case.
    diff = make_diff_output(files_touched=("src/foo.py", "src/unrelated_module.py"), lines_changed=5)
    backend = ScriptedAgentBackend(plans=[plan], diffs=[diff])
    orch = make_orchestrator(registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store, agent_backend=backend)

    status = orch.start_run(jira_key="PROJ-10", repo="acme/app", branch="feature/risk", trace_id="trace-10")
    status = orch.approve_plan(status.run_id, decision="approve")

    assert status.paused is True
    assert status.pause_kind == "checkpoint"
    assert status.checkpoint is not None
    assert status.checkpoint.trigger == "risk"
    assert status.checkpoint.details["out_of_scope_files"] == ["src/unrelated_module.py"]

    # Durable, not in-memory: the pending elicitation is on the Run row
    # itself, readable via a fresh get_run call.
    run = registry.get_run(tenant_id=tenant_id, run_id=status.run_id).data
    assert run.stage == "implementation"  # coarse stage unchanged; the pause lives in checkpoint_pointer
    assert run.checkpoint_pointer is not None
    assert '"trigger": "risk"' in run.checkpoint_pointer
    assert '"status": "pending"' in run.checkpoint_pointer


def test_mechanical_diff_is_against_declared_scope_not_agent_self_report(registry, tenant_id, plan_store, progress_store):
    """The check is a real set-difference against the plan artifact's
    `declared_scope.in_scope`, computed by the orchestrator -- not a
    judgment call the implementing agent reports on its own."""
    plan = make_plan_output(scope_in=("src/foo.py", "src/bar.py"))
    diff = make_diff_output(files_touched=("src/foo.py", "src/bar.py"), lines_changed=5)  # entirely in scope
    backend = ScriptedAgentBackend(plans=[plan], diffs=[diff])
    verifier = ScriptedVerificationRunner([VerificationResult(passed=True, summary="ok")])
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, verification_runner=verifier,
    )
    status = orch.start_run(jira_key="PROJ-11", repo="acme/app", branch="feature/in-scope", trace_id="trace-11")
    status = orch.approve_plan(status.run_id, decision="approve")
    # No risk checkpoint: every touched file was declared in scope.
    assert status.pause_kind == "gate"
    assert status.stage == "change_review_gate"


def test_resolve_checkpoint_extend_scope_regenerates_artifact_and_resumes(registry, tenant_id, plan_store, progress_store):
    plan = make_plan_output(scope_in=("src/foo.py",), subtasks=[SubTask(task_id="t1", description="x", parallel_group=None, depends_on=())])
    diff = make_diff_output(files_touched=("src/foo.py", "src/unrelated_module.py"), lines_changed=5)
    backend = ScriptedAgentBackend(plans=[plan], diffs=[diff])
    verifier = ScriptedVerificationRunner([VerificationResult(passed=True, summary="ok")])
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, verification_runner=verifier,
    )
    status = orch.start_run(jira_key="PROJ-12", repo="acme/app", branch="feature/extend", trace_id="trace-12")
    status = orch.approve_plan(status.run_id, decision="approve")
    assert status.pause_kind == "checkpoint"

    status = orch.resolve_checkpoint(status.run_id, decision="extend_scope")
    # Regenerated (bumped plan_version), not hand-patched, and the run
    # proceeds since the extended scope now covers the touched file.
    artifact = orch.plan_artifact(status.run_id)
    assert artifact["plan_version"] == 2
    assert "src/unrelated_module.py" in artifact["declared_scope"]["in_scope"]
    assert status.pause_kind == "gate"
    assert status.stage == "change_review_gate"


def test_resolve_checkpoint_stop_abandons_the_run(registry, tenant_id, plan_store, progress_store):
    plan = make_plan_output(scope_in=("src/foo.py",), subtasks=[SubTask(task_id="t1", description="x", parallel_group=None, depends_on=())])
    diff = make_diff_output(files_touched=("src/foo.py", "src/unrelated_module.py"), lines_changed=5)
    backend = ScriptedAgentBackend(plans=[plan], diffs=[diff])
    orch = make_orchestrator(registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store, agent_backend=backend)
    status = orch.start_run(jira_key="PROJ-13", repo="acme/app", branch="feature/stop", trace_id="trace-13")
    status = orch.approve_plan(status.run_id, decision="approve")
    assert status.pause_kind == "checkpoint"

    status = orch.resolve_checkpoint(status.run_id, decision="stop")
    assert status.stage == "abandoned"
    assert status.paused is False
