"""Stage-transition validation: legal transitions accepted, illegal ones
rejected with a clear, named error -- never silently accepted (F2
acceptance criteria)."""

from __future__ import annotations

from run_registry import stages
from run_registry.errors import ErrorCode
from run_registry.result import Outcome

from .conftest import make_run


def test_legal_transition_is_accepted(service, tenant_a):
    run = make_run(service, tenant_a)
    assert run.stage == stages.INTAKE

    result = service.transition_stage(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=run.version,
        next_stage=stages.RESEARCH,
    )
    assert result.outcome == Outcome.OK
    assert result.data.stage == stages.RESEARCH
    assert result.data.version == run.version + 1


def test_illegal_transition_is_rejected_with_clear_error(service, tenant_a):
    run = make_run(service, tenant_a)
    assert run.stage == stages.INTAKE

    # intake -> packaging is not a legal next step.
    result = service.transition_stage(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=run.version,
        next_stage=stages.PACKAGING,
    )
    assert result.outcome == Outcome.ERROR
    assert result.error.code == ErrorCode.ILLEGAL_TRANSITION
    assert "packaging" in result.error.message
    assert "intake" in result.error.message

    # And the run's stage must NOT have moved -- rejection, not silent
    # partial acceptance.
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.data.stage == stages.INTAKE
    assert reread.data.version == run.version


def test_illegal_transition_to_unknown_stage_is_rejected(service, tenant_a):
    run = make_run(service, tenant_a)
    result = service.transition_stage(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=run.version,
        next_stage="not_a_real_stage",
    )
    assert result.outcome == Outcome.ERROR
    assert result.error.code == ErrorCode.INVALID_INPUT


def test_terminal_stage_has_no_legal_outgoing_transition(service, tenant_a):
    run = make_run(service, tenant_a)
    v = run.version
    for stage in (stages.RESEARCH, stages.PLAN_AUTHORING, stages.PLAN_APPROVAL_GATE):
        r = service.transition_stage(
            tenant_id=tenant_a, run_id=run.id, expected_version=v, next_stage=stage
        )
        assert r.outcome == Outcome.OK
        v = r.data.version

    # Reject at the plan-approval gate -> abandoned (terminal).
    r = service.transition_stage(
        tenant_id=tenant_a, run_id=run.id, expected_version=v, next_stage=stages.ABANDONED
    )
    assert r.outcome == Outcome.OK
    assert r.data.stage == stages.ABANDONED
    v = r.data.version

    # No further transition is legal from a terminal stage.
    r2 = service.transition_stage(
        tenant_id=tenant_a, run_id=run.id, expected_version=v, next_stage=stages.INTAKE
    )
    assert r2.outcome == Outcome.ERROR
    assert r2.error.code == ErrorCode.ILLEGAL_TRANSITION


def test_full_happy_path_sequence_is_all_legal(service, tenant_a):
    run = make_run(service, tenant_a)
    v = run.version
    happy_path = [
        stages.RESEARCH,
        stages.PLAN_AUTHORING,
        stages.PLAN_APPROVAL_GATE,
        stages.IMPLEMENTATION,
        stages.VERIFICATION,
        stages.CHANGE_REVIEW_GATE,
        stages.PACKAGING,
        stages.RETROSPECTIVE,
        stages.COMPLETED,
    ]
    for stage in happy_path:
        r = service.transition_stage(
            tenant_id=tenant_a, run_id=run.id, expected_version=v, next_stage=stage
        )
        assert r.outcome == Outcome.OK, (stage, r)
        assert r.data.stage == stage
        v = r.data.version


def test_verification_failure_loops_back_to_implementation(service, tenant_a):
    run = make_run(service, tenant_a)
    v = run.version
    for stage in (stages.RESEARCH, stages.PLAN_AUTHORING, stages.PLAN_APPROVAL_GATE, stages.IMPLEMENTATION, stages.VERIFICATION):
        r = service.transition_stage(tenant_id=tenant_a, run_id=run.id, expected_version=v, next_stage=stage)
        assert r.outcome == Outcome.OK
        v = r.data.version

    r = service.transition_stage(
        tenant_id=tenant_a, run_id=run.id, expected_version=v, next_stage=stages.IMPLEMENTATION
    )
    assert r.outcome == Outcome.OK
    assert r.data.stage == stages.IMPLEMENTATION
