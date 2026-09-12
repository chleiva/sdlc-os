"""D10 adversarial pass on F2 (Run Registry Service) / D8 (Fleet
Dashboard) tenant_id enforcement -- the single choke point named in
spec Sec. 22's Rev 8 risk register entry ("Cross-tenant data leakage
via a shared control-plane bug").

Drives the REAL `run_registry.RegistryService` (SQLite-backed, a fresh
tmp_path DB per test) -- not a mock -- through every public method,
with a missing, wrong, and malformed tenant_id, on every operation
(get, list, write). Every case must fail closed: empty/denied, never an
error that reveals existence of another tenant's data.
"""
from __future__ import annotations

import uuid

import pytest

from run_registry import RegistryService
from run_registry.result import Outcome


@pytest.fixture
def service(tmp_path):
    svc = RegistryService(str(tmp_path / "registry.db"))
    yield svc
    svc.close()


@pytest.fixture
def tenant_a():
    return f"tenant-a-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tenant_b():
    return f"tenant-b-{uuid.uuid4().hex[:8]}"


def _make_run(service, tenant_id, **overrides):
    kwargs = dict(
        tenant_id=tenant_id, jira_key="PROJ-123", repo="org/repo", branch="feature/x",
        capacity_class="spot", trace_id=f"trace-{uuid.uuid4().hex[:8]}",
    )
    kwargs.update(overrides)
    result = service.create_run(**kwargs)
    assert result.outcome == Outcome.OK, result
    return result.data


MALFORMED_TENANT_IDS = [
    "",
    "   ",
    "\t",
    "' OR '1'='1",
    "'; DROP TABLE runs; --",
    "tenant-a\x00tenant-b",  # NUL-byte smuggling attempt
]


# ---------------------------------------------------------------------
# get_run: missing / wrong / malformed tenant_id, all fail closed.
# ---------------------------------------------------------------------
def test_get_run_wrong_tenant_is_empty_not_error(service, tenant_a, tenant_b):
    run = _make_run(service, tenant_a)
    result = service.get_run(tenant_id=tenant_b, run_id=run.id)
    assert result.outcome == Outcome.EMPTY
    assert result.data is None
    assert result.error is None


@pytest.mark.parametrize("bad_tenant", MALFORMED_TENANT_IDS)
def test_get_run_malformed_tenant_id_never_leaks_tenant_a_data(service, tenant_a, bad_tenant):
    run = _make_run(service, tenant_a)
    result = service.get_run(tenant_id=bad_tenant, run_id=run.id)
    # Fail closed: never OK, never leaks tenant_a's row.
    assert result.outcome in (Outcome.EMPTY, Outcome.ERROR)
    assert result.data is None


def test_get_run_wrong_tenant_indistinguishable_from_nonexistent_run(service, tenant_a, tenant_b):
    run = _make_run(service, tenant_a)
    wrong_tenant = service.get_run(tenant_id=tenant_b, run_id=run.id)
    nonexistent = service.get_run(tenant_id=tenant_b, run_id="not-a-real-run-id")
    assert wrong_tenant == nonexistent


# ---------------------------------------------------------------------
# list_runs: missing / wrong / malformed tenant_id, all fail closed.
# ---------------------------------------------------------------------
def test_list_runs_wrong_tenant_sees_nothing(service, tenant_a, tenant_b):
    _make_run(service, tenant_a)
    result = service.list_runs(tenant_id=tenant_b)
    assert result.outcome == Outcome.EMPTY
    assert result.data is None


@pytest.mark.parametrize("bad_tenant", MALFORMED_TENANT_IDS)
def test_list_runs_malformed_tenant_id_sees_nothing(service, tenant_a, bad_tenant):
    _make_run(service, tenant_a)
    result = service.list_runs(tenant_id=bad_tenant)
    assert result.outcome == Outcome.EMPTY
    assert not result.data


def test_list_runs_correct_tenant_sees_only_its_own_runs(service, tenant_a, tenant_b):
    run_a = _make_run(service, tenant_a)
    run_b = _make_run(service, tenant_b)
    result_a = service.list_runs(tenant_id=tenant_a)
    assert result_a.outcome == Outcome.OK
    ids_a = {r.id for r in result_a.data}
    assert run_a.id in ids_a
    assert run_b.id not in ids_a


# ---------------------------------------------------------------------
# list_attempts: wrong/malformed tenant_id never leaks another
# tenant's attempt history via a guessed run_id.
# ---------------------------------------------------------------------
def test_list_attempts_wrong_tenant_returns_empty(service, tenant_a, tenant_b):
    run = _make_run(service, tenant_a)
    result = service.list_attempts(tenant_id=tenant_b, run_id=run.id)
    assert result.outcome == Outcome.EMPTY
    assert result.data is None


@pytest.mark.parametrize("bad_tenant", MALFORMED_TENANT_IDS)
def test_list_attempts_malformed_tenant_id_returns_empty(service, tenant_a, bad_tenant):
    run = _make_run(service, tenant_a)
    result = service.list_attempts(tenant_id=bad_tenant, run_id=run.id)
    assert result.outcome == Outcome.EMPTY


# ---------------------------------------------------------------------
# append_attempt (a write): wrong tenant_id must not mutate tenant A's
# run, and must not succeed.
# ---------------------------------------------------------------------
def test_append_attempt_wrong_tenant_is_rejected_and_does_not_mutate(service, tenant_a, tenant_b):
    run = _make_run(service, tenant_a)
    result = service.append_attempt(
        tenant_id=tenant_b, run_id=run.id, expected_version=run.version,
        reason="retry", starting_stage=run.stage, trace_id="attacker-trace",
    )
    assert result.outcome != Outcome.OK
    # Confirm tenant A's run is provably untouched.
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.outcome == Outcome.OK
    assert reread.data.version == run.version
    assert reread.data.stage == run.stage


@pytest.mark.parametrize("bad_tenant", MALFORMED_TENANT_IDS)
def test_append_attempt_malformed_tenant_id_is_rejected_and_does_not_mutate(service, tenant_a, bad_tenant):
    run = _make_run(service, tenant_a)
    result = service.append_attempt(
        tenant_id=bad_tenant, run_id=run.id, expected_version=run.version,
        reason="retry", starting_stage=run.stage, trace_id="attacker-trace",
    )
    assert result.outcome != Outcome.OK
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.data.version == run.version


# ---------------------------------------------------------------------
# transition_stage (a write): wrong/malformed tenant_id must not
# advance tenant A's run's stage.
# ---------------------------------------------------------------------
def test_transition_stage_wrong_tenant_is_rejected_not_found_not_error_leak(service, tenant_a, tenant_b):
    run = _make_run(service, tenant_a)
    result = service.transition_stage(
        tenant_id=tenant_b, run_id=run.id, expected_version=run.version, next_stage="research"
    )
    assert result.outcome == Outcome.EMPTY
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.data.stage == "intake"
    assert reread.data.version == run.version


@pytest.mark.parametrize("bad_tenant", MALFORMED_TENANT_IDS)
def test_transition_stage_malformed_tenant_id_does_not_mutate(service, tenant_a, bad_tenant):
    run = _make_run(service, tenant_a)
    result = service.transition_stage(
        tenant_id=bad_tenant, run_id=run.id, expected_version=run.version, next_stage="research"
    )
    assert result.outcome != Outcome.OK
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.data.stage == "intake"


# ---------------------------------------------------------------------
# write_checkpoint / write_execution_location (writes): same fail-
# closed discipline.
# ---------------------------------------------------------------------
def test_write_checkpoint_wrong_tenant_does_not_leak_or_mutate(service, tenant_a, tenant_b):
    run = _make_run(service, tenant_a)
    result = service.write_checkpoint(
        tenant_id=tenant_b, run_id=run.id, expected_version=run.version, checkpoint_pointer="attacker-payload"
    )
    assert result.outcome == Outcome.EMPTY
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.data.checkpoint_pointer is None


def test_write_execution_location_wrong_tenant_does_not_mutate(service, tenant_a, tenant_b):
    run = _make_run(service, tenant_a)
    result = service.write_execution_location(
        tenant_id=tenant_b, run_id=run.id, expected_version=run.version, node_id="attacker-node"
    )
    assert result.outcome == Outcome.EMPTY
    reread = service.get_run(tenant_id=tenant_a, run_id=run.id)
    assert reread.data.execution_location.node_id is None


# ---------------------------------------------------------------------
# create_run (a write): missing tenant_id is INVALID_INPUT (not EMPTY)
# -- a deliberate, documented asymmetry vs. every read path. Confirm it
# still fails closed (no row created) rather than defaulting to some
# shared/anonymous tenant.
# ---------------------------------------------------------------------
def test_create_run_missing_tenant_id_is_rejected_not_defaulted(service):
    result = service.create_run(
        tenant_id="", jira_key="PROJ-1", repo="org/repo", branch="b", capacity_class="spot", trace_id="t1"
    )
    assert result.outcome == Outcome.ERROR
    # And no run was actually created under any accessible tenant.
    any_tenant_listing = service.list_runs(tenant_id="")
    assert any_tenant_listing.outcome == Outcome.EMPTY


def test_create_run_whitespace_tenant_id_is_isolated_from_real_tenants(service, tenant_a):
    """Whitespace-only is truthy in Python (not caught by `if not
    tenant_id`) so create_run currently accepts it -- document and
    confirm this odd-but-real tenant value is still fully isolated from
    tenant_a's own data, i.e. it behaves like any other distinct
    tenant_id string, never as a wildcard/shared bucket."""
    weird_tenant = "   "
    result = service.create_run(
        tenant_id=weird_tenant, jira_key="PROJ-1", repo="org/repo", branch="b", capacity_class="spot", trace_id="t1"
    )
    _make_run(service, tenant_a)
    if result.outcome == Outcome.OK:
        # It was accepted as a distinct tenant -- prove isolation holds.
        weird_listing = service.list_runs(tenant_id=weird_tenant)
        assert weird_listing.outcome == Outcome.OK
        assert all(r.tenant_id == weird_tenant for r in weird_listing.data)
        tenant_a_listing = service.list_runs(tenant_id=tenant_a)
        assert all(r.tenant_id == tenant_a for r in tenant_a_listing.data)
    else:
        # Or it was rejected outright -- also acceptable (fail closed).
        assert result.outcome == Outcome.ERROR


# ---------------------------------------------------------------------
# Type-confusion: a non-string tenant_id must never coincide with its
# string representation belonging to a different, real tenant.
# ---------------------------------------------------------------------
def test_integer_tenant_id_is_not_confused_with_its_string_form(service):
    string_tenant = "123"
    _make_run(service, string_tenant)
    # An int tenant_id is a type-confusion attempt, not a supported
    # input -- it must not incidentally match the string tenant "123"'s
    # data.
    result = service.get_run(tenant_id=123, run_id="whatever")  # type: ignore[arg-type]
    assert result.outcome != Outcome.OK
    listing = service.list_runs(tenant_id=123)  # type: ignore[arg-type]
    assert listing.outcome != Outcome.OK or not any(r.tenant_id == string_tenant for r in (listing.data or []))
