"""Attempt records are append-only and queryable in full per Run (spec
Section 14.14 / F2 acceptance criteria)."""

from __future__ import annotations

import uuid

from run_registry import stages
from run_registry.errors import ErrorCode
from run_registry.result import Outcome

from .conftest import make_run


def test_initial_attempt_is_created_with_the_run(service, tenant_a):
    run = make_run(service, tenant_a)

    attempts = service.list_attempts(tenant_id=tenant_a, run_id=run.id)
    assert attempts.outcome == Outcome.OK
    assert len(attempts.data) == 1
    assert attempts.data[0].reason == "initial"
    assert attempts.data[0].starting_stage == stages.INTAKE
    assert attempts.data[0].end_ts is None


def test_append_attempt_preserves_prior_attempt_history(service, tenant_a):
    run = make_run(service, tenant_a)
    v = run.version
    for stage in (stages.RESEARCH, stages.PLAN_AUTHORING, stages.PLAN_APPROVAL_GATE):
        r = service.transition_stage(tenant_id=tenant_a, run_id=run.id, expected_version=v, next_stage=stage)
        v = r.data.version

    # "Request changes" at the plan-approval gate -> re-plan Attempt.
    appended = service.append_attempt(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=v,
        reason="re-plan",
        starting_stage=stages.PLAN_AUTHORING,
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
    )
    assert appended.outcome == Outcome.OK
    assert appended.data.reason == "re-plan"
    assert appended.data.attempt_number == 2

    history = service.list_attempts(tenant_id=tenant_a, run_id=run.id)
    assert history.outcome == Outcome.OK
    assert len(history.data) == 2

    first, second = history.data
    # First attempt's original fields must be untouched (append-only).
    assert first.attempt_number == 1
    assert first.reason == "initial"
    assert first.starting_stage == stages.INTAKE
    # The first attempt should now be closed (end_ts set) once the second
    # started, but its start_ts/reason/trace_id must be exactly as
    # originally recorded.
    assert first.end_ts is not None

    assert second.attempt_number == 2
    assert second.reason == "re-plan"
    assert second.starting_stage == stages.PLAN_AUTHORING
    assert second.end_ts is None

    # The Run's current stage reflects the latest Attempt.
    run_now = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert run_now.data.stage == stages.PLAN_AUTHORING
    assert run_now.data.current_attempt_id == second.id


def test_multiple_restarts_accumulate_full_queryable_history(service, tenant_a):
    run = make_run(service, tenant_a)
    v = run.version

    reasons = ["retry", "resumed-after-interruption", "re-plan"]
    stage_targets = [stages.INTAKE, stages.INTAKE, stages.INTAKE]
    for reason, target in zip(reasons, stage_targets):
        r = service.append_attempt(
            tenant_id=tenant_a,
            run_id=run.id,
            expected_version=v,
            reason=reason,
            starting_stage=target,
            trace_id=f"trace-{uuid.uuid4().hex[:8]}",
        )
        assert r.outcome == Outcome.OK, r
        v = service.get_run(tenant_id=tenant_a, run_id=run.id).data.version

    history = service.list_attempts(tenant_id=tenant_a, run_id=run.id)
    assert history.outcome == Outcome.OK
    assert [a.attempt_number for a in history.data] == [1, 2, 3, 4]
    assert [a.reason for a in history.data] == ["initial", "retry", "resumed-after-interruption", "re-plan"]
    # Only the very last attempt is still open.
    assert all(a.end_ts is not None for a in history.data[:-1])
    assert history.data[-1].end_ts is None


def test_append_attempt_rejects_invalid_reason(service, tenant_a):
    run = make_run(service, tenant_a)
    result = service.append_attempt(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=run.version,
        reason="because-i-felt-like-it",
        starting_stage=stages.INTAKE,
        trace_id="trace-x",
    )
    assert result.outcome == Outcome.ERROR
    assert result.error.code == ErrorCode.INVALID_INPUT


def test_append_attempt_rejects_illegal_starting_stage(service, tenant_a):
    run = make_run(service, tenant_a)
    # Run is at 'intake'; jumping an attempt straight to 'packaging' is
    # not a legal transition.
    result = service.append_attempt(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=run.version,
        reason="retry",
        starting_stage=stages.PACKAGING,
        trace_id="trace-x",
    )
    assert result.outcome == Outcome.ERROR
    assert result.error.code == ErrorCode.ILLEGAL_TRANSITION


def test_append_attempt_stale_version_is_rejected(service, tenant_a):
    run = make_run(service, tenant_a)
    # Bump the version out from under the caller.
    service.transition_stage(
        tenant_id=tenant_a, run_id=run.id, expected_version=run.version, next_stage=stages.RESEARCH
    )

    result = service.append_attempt(
        tenant_id=tenant_a,
        run_id=run.id,
        expected_version=run.version,  # stale
        reason="retry",
        starting_stage=stages.RESEARCH,
        trace_id="trace-x",
    )
    assert result.outcome == Outcome.ERROR
    assert result.error.code == ErrorCode.STALE_VERSION
