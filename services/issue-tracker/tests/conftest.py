from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))  # for `mocks.jira_mock_server` / `mocks.rovo_mock_server`

from mocks.jira_mock_server import start_mock_server as start_jira_mock  # noqa: E402
from mocks.rovo_mock_server import start_mock_server as start_rovo_mock  # noqa: E402

from issue_tracker.config import TenantJiraConfig  # noqa: E402
from issue_tracker.jira_client import JiraClient  # noqa: E402
from issue_tracker.rovo_client import RovoClient, RovoConfig  # noqa: E402


@pytest.fixture
def jira_mock():
    server, store = start_jira_mock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}", store
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def tenant_config(jira_mock):
    base_url, _store = jira_mock
    return TenantJiraConfig(
        tenant_id="tenant-acme",
        base_url=base_url,
        auth_mode="oauth_bearer",
        oauth_bearer_token="test-token",
        project_key="PROJ",
        opt_in_label="ai-factory",
        trigger_status="Selected for Development",
        approval_status="In Progress",
        change_review_status="In Review",
    )


@pytest.fixture
def jira_client(tenant_config):
    return JiraClient(config=tenant_config)


@pytest.fixture
def rovo_mock():
    server, store = start_rovo_mock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}", store
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def rovo_client(rovo_mock):
    base_url, _store = rovo_mock
    return RovoClient(config=RovoConfig(tenant_id="tenant-acme", base_url=base_url, oauth_bearer_token="rovo-token"))
