from __future__ import annotations

import uuid

import pytest
from run_registry import RegistryService
from run_registry.result import Outcome

from fleet_dashboard.dashboard_service import DashboardService
from fleet_dashboard.poll_state import PollHistory


@pytest.fixture
def registry(tmp_path):
    db_path = str(tmp_path / "registry.db")
    svc = RegistryService(db_path)
    yield svc
    svc.close()


@pytest.fixture
def dashboard(registry):
    return DashboardService(registry, poll_history=PollHistory())


@pytest.fixture
def tenant_a():
    return f"tenant-a-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tenant_b():
    return f"tenant-b-{uuid.uuid4().hex[:8]}"


def make_run(registry, tenant_id, **overrides):
    kwargs = dict(
        tenant_id=tenant_id,
        jira_key=f"PROJ-{uuid.uuid4().hex[:4]}",
        repo="org/repo",
        branch="feature/x",
        capacity_class="spot",
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
    )
    kwargs.update(overrides)
    result = registry.create_run(**kwargs)
    assert result.outcome == Outcome.OK, result
    return result.data


def advance(registry, run, next_stage):
    result = registry.transition_stage(
        tenant_id=run.tenant_id,
        run_id=run.id,
        expected_version=run.version,
        next_stage=next_stage,
    )
    assert result.outcome == Outcome.OK, result
    return result.data
