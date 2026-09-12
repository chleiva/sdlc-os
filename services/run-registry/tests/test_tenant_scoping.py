"""Tenant scoping must fail closed (spec Section 14.13 / F2 acceptance
criteria): a missing or wrong tenant_id returns an empty result, never an
error, and never leaks whether a run exists for another tenant."""

from __future__ import annotations

from run_registry.result import Outcome

from .conftest import make_run


def test_get_run_wrong_tenant_returns_empty_not_error(service, tenant_a, tenant_b):
    run = make_run(service, tenant_a)

    result = service.get_run(tenant_id=tenant_b, run_id=run.id)
    assert result.outcome == Outcome.EMPTY
    assert result.error is None
    assert result.data is None


def test_get_run_missing_tenant_id_returns_empty(service, tenant_a):
    run = make_run(service, tenant_a)

    result = service.get_run(tenant_id="", run_id=run.id)
    assert result.outcome == Outcome.EMPTY


def test_get_run_wrong_tenant_is_indistinguishable_from_nonexistent_run(service, tenant_a, tenant_b):
    """The whole point of fail-closed scoping: a caller cannot tell
    "wrong tenant" apart from "no such run at all" -- both must produce
    byte-identical Result shapes."""
    run = make_run(service, tenant_a)

    wrong_tenant_result = service.get_run(tenant_id=tenant_b, run_id=run.id)
    nonexistent_result = service.get_run(tenant_id=tenant_b, run_id="not-a-real-run-id")

    assert wrong_tenant_result == nonexistent_result


def test_list_runs_wrong_tenant_sees_nothing(service, tenant_a, tenant_b):
    make_run(service, tenant_a)
    make_run(service, tenant_a)

    result = service.list_runs(tenant_id=tenant_b)
    assert result.outcome == Outcome.EMPTY
    assert result.data is None


def test_list_runs_missing_tenant_id_sees_nothing(service, tenant_a):
    make_run(service, tenant_a)

    result = service.list_runs(tenant_id="")
    assert result.outcome == Outcome.EMPTY


def test_list_runs_correct_tenant_sees_only_its_own_runs(service, tenant_a, tenant_b):
    run_a1 = make_run(service, tenant_a)
    run_a2 = make_run(service, tenant_a)
    run_b1 = make_run(service, tenant_b)

    result_a = service.list_runs(tenant_id=tenant_a)
    assert result_a.outcome == Outcome.OK
    ids_a = {r.id for r in result_a.data}
    assert ids_a == {run_a1.id, run_a2.id}
    assert run_b1.id not in ids_a

    result_b = service.list_runs(tenant_id=tenant_b)
    assert result_b.outcome == Outcome.OK
    assert {r.id for r in result_b.data} == {run_b1.id}


def test_list_attempts_wrong_tenant_returns_empty(service, tenant_a, tenant_b):
    run = make_run(service, tenant_a)

    result = service.list_attempts(tenant_id=tenant_b, run_id=run.id)
    assert result.outcome == Outcome.EMPTY


def test_transition_stage_wrong_tenant_is_rejected_as_not_found_not_error(service, tenant_a, tenant_b):
    from run_registry import stages

    run = make_run(service, tenant_a)

    result = service.transition_stage(
        tenant_id=tenant_b,
        run_id=run.id,
        expected_version=run.version,
        next_stage=stages.RESEARCH,
    )
    # Fail closed: this must be EMPTY (not-found-shaped), never an error
    # that would confirm the run exists under a different tenant.
    assert result.outcome == Outcome.EMPTY

    # And the run must be provably untouched.
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.data.version == run.version
    assert reread.data.stage == run.stage


def test_write_checkpoint_wrong_tenant_does_not_leak_or_mutate(service, tenant_a, tenant_b):
    run = make_run(service, tenant_a)

    result = service.write_checkpoint(
        tenant_id=tenant_b,
        run_id=run.id,
        expected_version=run.version,
        checkpoint_pointer="s3://bucket/checkpoint-1",
    )
    assert result.outcome == Outcome.EMPTY

    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.data.checkpoint_pointer is None
    assert reread.data.version == run.version
