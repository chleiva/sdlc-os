from __future__ import annotations

import uuid

import pytest
from run_registry import RegistryService
from run_registry.result import Outcome

from tenant_cell.clock import FakeClock


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def registry(tmp_path):
    db_path = str(tmp_path / "registry.db")
    svc = RegistryService(db_path)
    yield svc
    svc.close()


@pytest.fixture
def tenant_id() -> str:
    return f"tenant-{uuid.uuid4().hex[:8]}"


def make_run(registry: RegistryService, tenant_id: str, **overrides):
    kwargs = dict(
        tenant_id=tenant_id,
        jira_key="PROJ-1",
        repo="org/repo",
        branch="feature/x",
        capacity_class="spot",
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
    )
    kwargs.update(overrides)
    result = registry.create_run(**kwargs)
    assert result.outcome == Outcome.OK, result
    return result.data
