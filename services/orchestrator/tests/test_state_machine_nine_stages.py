"""Drives a run through every one of Section 5's nine stages (plus the
terminal Completed stage) end to end, using the deterministic scripted
`AgentBackend`/`VerificationRunner` mocks -- no live model, no live CI.
Every stage transition is written through the real `RegistryService`
(F2), not reimplemented."""
from __future__ import annotations

from orchestrator.model_backend import ScriptedAgentBackend
from orchestrator.verification import ScriptedVerificationRunner, VerificationResult

from ._factories import make_diff_output, make_plan_output
from .conftest import make_orchestrator


def test_full_nine_stage_happy_path(registry, tenant_id, plan_store, progress_store):
    plan = make_plan_output()
    diff = make_diff_output()
    backend = ScriptedAgentBackend(plans=[plan], diffs=[diff])
    verifier = ScriptedVerificationRunner([VerificationResult(passed=True, summary="all green")])
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, verification_runner=verifier,
    )

    # Stage 1 (intake) -> 2 (research) -> 3 (plan authoring) -> 4 (gate, paused)
    status = orch.start_run(jira_key="PROJ-1", repo="acme/app", branch="feature/x", trace_id="trace-1")
    assert status.paused is True
    assert status.pause_kind == "gate"
    assert status.stage == "plan_approval_gate"

    artifact = orch.plan_artifact(status.run_id)
    assert artifact is not None
    assert artifact["plan_version"] == 1
    assert artifact["declared_scope"]["in_scope"] == ["src/foo.py", "tests/test_foo.py"]
    assert artifact["risk"]["story_size"] == "S"

    # Gate approved -> Stage 5 (implementation) -> Stage 6 (verification,
    # scripted pass) -> Stage 7 (change review gate, paused).
    status = orch.approve_plan(status.run_id, decision="approve")
    assert status.paused is True
    assert status.pause_kind == "gate"
    assert status.stage == "change_review_gate"
    assert backend.implement_call_count == 1

    # Gate approved -> Stage 8 (packaging) -> Stage 9 (retrospective) ->
    # terminal Completed.
    status = orch.approve_change_review(status.run_id, decision="approve")
    assert status.paused is False
    assert status.pause_kind is None
    assert status.stage == "completed"

    run = registry.get_run(tenant_id=tenant_id, run_id=status.run_id).data
    assert run.stage == "completed"


def test_change_review_gate_request_changes_loops_back_to_implementation(registry, tenant_id, plan_store, progress_store):
    plan = make_plan_output()
    diffs = [make_diff_output(lines_changed=5), make_diff_output(lines_changed=5, subtask_id="t1-retry")]
    backend = ScriptedAgentBackend(plans=[plan], diffs=diffs)
    verifier = ScriptedVerificationRunner([VerificationResult(passed=True, summary="ok")])
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, verification_runner=verifier,
    )
    status = orch.start_run(jira_key="PROJ-2", repo="acme/app", branch="feature/y", trace_id="trace-2")
    status = orch.approve_plan(status.run_id, decision="approve")
    assert status.stage == "change_review_gate"

    # Section 5 Stage 7: "request changes" sends it back to implementation,
    # not to plan authoring (see run_registry.stages module docstring).
    status = orch.approve_change_review(status.run_id, decision="request_changes")
    assert status.stage == "change_review_gate"
    assert status.paused is True


def test_plan_gate_reject_abandons_the_run(registry, tenant_id, plan_store, progress_store):
    backend = ScriptedAgentBackend(plans=[make_plan_output()])
    orch = make_orchestrator(registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store, agent_backend=backend)
    status = orch.start_run(jira_key="PROJ-3", repo="acme/app", branch="feature/z", trace_id="trace-3")
    status = orch.approve_plan(status.run_id, decision="reject")
    assert status.stage == "abandoned"
    assert status.paused is False


def test_plan_gate_request_changes_starts_a_new_attempt_and_regenerates_the_artifact(registry, tenant_id, plan_store, progress_store):
    plan_v1 = make_plan_output(scope_in=("src/foo.py",))
    plan_v2 = make_plan_output(scope_in=("src/foo.py", "src/foo_helpers.py"))
    backend = ScriptedAgentBackend(plans=[plan_v1, plan_v2], diffs=[make_diff_output()])
    verifier = ScriptedVerificationRunner([VerificationResult(passed=True, summary="ok")])
    orch = make_orchestrator(
        registry=registry, tenant_id=tenant_id, plan_store=plan_store, progress_store=progress_store,
        agent_backend=backend, verification_runner=verifier,
    )
    status = orch.start_run(jira_key="PROJ-4", repo="acme/app", branch="feature/w", trace_id="trace-4")
    artifact_v1 = orch.plan_artifact(status.run_id)
    assert artifact_v1["plan_version"] == 1

    status = orch.approve_plan(status.run_id, decision="request_changes", feedback="also touch the helpers module")
    assert status.stage == "plan_approval_gate"
    assert status.paused is True

    artifact_v2 = orch.plan_artifact(status.run_id)
    assert artifact_v2["plan_version"] == 2
    assert artifact_v2["declared_scope"]["in_scope"] == ["src/foo.py", "src/foo_helpers.py"]

    attempts = registry.list_attempts(tenant_id=tenant_id, run_id=status.run_id).data
    assert [a.reason for a in attempts] == ["initial", "re-plan"]

    status = orch.approve_plan(status.run_id, decision="approve")
    assert status.stage == "change_review_gate"
