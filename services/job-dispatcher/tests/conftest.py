from __future__ import annotations

import sys
import threading
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# D4's real mock Jira server + real config/client -- job-dispatcher's
# own tests post the capacity-delay comment against this, exactly as
# the brief requires ("against its own mock Jira, in your tests").
ISSUE_TRACKER_ROOT = ROOT.parent / "issue-tracker"
sys.path.insert(0, str(ISSUE_TRACKER_ROOT))

from mocks.jira_mock_server import MockIssue, start_mock_server as start_jira_mock  # noqa: E402

from issue_tracker.config import TenantJiraConfig  # noqa: E402
from issue_tracker.jira_client import JiraClient  # noqa: E402
from issue_tracker import webhook_signing  # noqa: E402

from run_registry import RegistryService  # noqa: E402

from job_dispatcher.capacity import MockCapacityProvider  # noqa: E402
from job_dispatcher.dispatcher import JobDispatcher  # noqa: E402
from job_dispatcher.tenant_resolution import TenantDirectory  # noqa: E402

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
PROJECT_A = "PROJA"
PROJECT_B = "PROJB"
SECRET_A = b"tenant-a-webhook-secret-0123456789"
SECRET_B = b"tenant-b-webhook-secret-9876543210"


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
def jira_configs(jira_mock):
    base_url, _store = jira_mock
    return {
        TENANT_A: TenantJiraConfig(
            tenant_id=TENANT_A,
            base_url=base_url,
            auth_mode="oauth_bearer",
            oauth_bearer_token="test-token-a",
            project_key=PROJECT_A,
        ),
        TENANT_B: TenantJiraConfig(
            tenant_id=TENANT_B,
            base_url=base_url,
            auth_mode="oauth_bearer",
            oauth_bearer_token="test-token-b",
            project_key=PROJECT_B,
        ),
    }


@pytest.fixture
def jira_client_for_tenant(jira_configs):
    cache: dict[str, JiraClient] = {}

    def _for_tenant(tenant_id: str) -> JiraClient:
        if tenant_id not in cache:
            cache[tenant_id] = JiraClient(config=jira_configs[tenant_id])
        return cache[tenant_id]

    return _for_tenant


@pytest.fixture
def registry(tmp_path):
    svc = RegistryService(str(tmp_path / "registry.db"))
    yield svc
    svc.close()


@pytest.fixture
def secrets():
    return {TENANT_A: SECRET_A, TENANT_B: SECRET_B}


@pytest.fixture
def secret_lookup(secrets):
    def _lookup(tenant_id: str) -> bytes | None:
        return secrets.get(tenant_id)

    return _lookup


@pytest.fixture
def tenant_directory():
    return TenantDirectory.from_mapping({PROJECT_A: TENANT_A, PROJECT_B: TENANT_B})


@pytest.fixture
def capacity_provider():
    return MockCapacityProvider()


@pytest.fixture
def dispatcher(secret_lookup, tenant_directory, registry, capacity_provider, jira_client_for_tenant):
    return JobDispatcher(
        secret_lookup=secret_lookup,
        tenant_directory=tenant_directory,
        registry=registry,
        capacity_provider=capacity_provider,
        jira_client_for_tenant=jira_client_for_tenant,
    )


def seed_issue(store, *, project_key: str, key_hint: str | None = None) -> str:
    """Seed a MockIssue and return its key -- job-dispatcher's own
    tests don't call `create_epic`/`create_story`, they just need an
    issue key that exists for `post_comment` to succeed against."""
    key = key_hint or f"{project_key}-{uuid.uuid4().hex[:6]}"
    store.seed(
        MockIssue(
            key=key,
            project_key=project_key,
            issue_type="Story",
            summary="A triggered story",
            description={"type": "doc", "version": 1, "content": []},
        )
    )
    return key


def signed_webhook(*, tenant_id: str, secret: bytes, body_obj: dict, now: float | None = None):
    """Build (headers, body) for a valid Sec. 17.3 signed request,
    reusing D4's real `sign_request` -- the exact function the relay
    (`issue_tracker.webhook_relay`) uses to sign a real dispatch."""
    import json

    body = json.dumps(body_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    headers = webhook_signing.sign_request(tenant_id=tenant_id, secret=secret, body=body, now=now)
    headers["Content-Type"] = "application/json"
    return headers, body
