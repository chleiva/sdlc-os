from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

import run_registry.repository as repository
from run_registry import RegistryService
from run_registry.result import Outcome


@pytest.fixture
def registry(tmp_path):
    db_path = str(tmp_path / "registry.db")
    svc = RegistryService(db_path)
    yield svc
    svc.close()


@pytest.fixture
def tenant_id():
    return f"tenant-{uuid.uuid4().hex[:8]}"


def create_run(registry, tenant_id, **overrides):
    kwargs = dict(
        jira_key=f"PROJ-{uuid.uuid4().hex[:6]}",
        repo="org/repo",
        branch="feature/x",
        capacity_class="spot",
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
    )
    kwargs.update(overrides)
    result = registry.create_run(tenant_id=tenant_id, **kwargs)
    assert result.outcome == Outcome.OK, result
    return result.data


class FakeClock:
    """Controls the timestamp `run_registry.repository.now_iso()` returns,
    so tests can build realistic multi-hour/multi-day Run/Attempt
    histories through REAL `RegistryService` calls (create_run,
    transition_stage, append_attempt -- all real, unmodified operations)
    without actually sleeping in wall-clock time.

    This mocks time itself, not the Registry Service: every write still
    goes through the real service/repository code path and is subject to
    its real validation (legal-transition checks, optimistic-concurrency
    version checks, tenant scoping). Only the value `now_iso()` returns is
    controlled.
    """

    def __init__(self, start: datetime):
        self._now = start

    def now_iso(self) -> str:
        return self._now.isoformat()

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = dt


@pytest.fixture
def clock(monkeypatch):
    fc = FakeClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(repository, "now_iso", fc.now_iso)
    return fc


def transition(registry, tenant_id, run, next_stage):
    """Real `RegistryService.transition_stage` call; returns the updated
    Run and asserts it succeeded (fails loudly, not silently, on a bad
    test setup)."""
    result = registry.transition_stage(
        tenant_id=tenant_id,
        run_id=run.id,
        expected_version=run.version,
        next_stage=next_stage,
    )
    assert result.outcome == Outcome.OK, result
    return result.data


def new_attempt(registry, tenant_id, run, *, reason, starting_stage, trace_id=None):
    result = registry.append_attempt(
        tenant_id=tenant_id,
        run_id=run.id,
        expected_version=run.version,
        reason=reason,
        starting_stage=starting_stage,
        trace_id=trace_id or f"trace-{uuid.uuid4().hex[:8]}",
    )
    assert result.outcome == Outcome.OK, result
    updated_run = registry.get_run(tenant_id=tenant_id, run_id=run.id).data
    return updated_run, result.data
