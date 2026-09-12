from __future__ import annotations

import uuid

import pytest

from run_registry import RegistryService
from run_registry.result import Outcome


@pytest.fixture
def service(tmp_path):
    db_path = str(tmp_path / "registry.db")
    svc = RegistryService(db_path)
    yield svc
    svc.close()


@pytest.fixture
def tenant_a():
    return f"tenant-a-{uuid.uuid4().hex[:8]}"


@pytest.fixture
def tenant_b():
    return f"tenant-b-{uuid.uuid4().hex[:8]}"


def make_run(service, tenant_id, **overrides):
    kwargs = dict(
        tenant_id=tenant_id,
        jira_key="PROJ-123",
        repo="org/repo",
        branch="feature/x",
        capacity_class="spot",
        trace_id=f"trace-{uuid.uuid4().hex[:8]}",
    )
    kwargs.update(overrides)
    result = service.create_run(**kwargs)
    assert result.outcome == Outcome.OK, result
    return result.data
