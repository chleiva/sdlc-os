from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import pytest

GATES_ROOT = Path(__file__).resolve().parent.parent
SERVICES_ROOT = GATES_ROOT.parent

# -- issue-tracker (D4): real JiraClient + its own local mock server --------
ISSUE_TRACKER_ROOT = SERVICES_ROOT / "issue-tracker"
sys.path.insert(0, str(ISSUE_TRACKER_ROOT))  # for `mocks.jira_mock_server`

from mocks.jira_mock_server import start_mock_server as start_jira_mock  # noqa: E402

from issue_tracker.config import TenantJiraConfig  # noqa: E402
from issue_tracker.jira_client import JiraClient  # noqa: E402


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
def tenant_jira_config(jira_mock):
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
def jira_client(tenant_jira_config):
    return JiraClient(config=tenant_jira_config)


# -- source-control (D5): real SourceControlService + its own mock GitHub ---
SOURCE_CONTROL_ROOT = SERVICES_ROOT / "source-control"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_mock_github_module = _load_module(
    "gates_test_support.mock_github_server", SOURCE_CONTROL_ROOT / "tests" / "mock_github_server.py"
)
MockGitHubServer = _mock_github_module.MockGitHubServer

from source_control.audit import AuditLogger as SourceControlAuditLogger  # noqa: E402
from source_control.service import (  # noqa: E402
    InstallationRegistry,
    SourceControlService,
    TenantInstallation,
)


@pytest.fixture(scope="session")
def rsa_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_key, private_key.public_key(), private_pem


@pytest.fixture
def mock_github(rsa_keypair):
    _, public_key, _ = rsa_keypair
    server = MockGitHubServer(app_id="918273", app_public_key=public_key)
    base_url = server.start()
    try:
        yield server, base_url
    finally:
        server.stop()


@pytest.fixture
def source_control_service(rsa_keypair, mock_github, tmp_path):
    _, _, private_pem = rsa_keypair
    _, base_url = mock_github
    installation = TenantInstallation(
        tenant_id="tenant-acme",
        installation_id="inst-42",
        app_id="918273",
        app_slug="sdlc-auto",
        private_key_pem=private_pem,
        allowed_repositories=frozenset({"acme/app"}),
        mirror_root=tmp_path / "mirrors",
        api_base_url=base_url,
    )
    registry = InstallationRegistry()
    registry.register(installation)
    return SourceControlService(registry, audit_logger=SourceControlAuditLogger())


# -- index-server (D3): real RepoIndex / CODEOWNERS engine -------------------
from index_server.engine.repo_index import RepoIndex  # noqa: E402


@pytest.fixture
def codeowners_repo(tmp_path):
    """A minimal on-disk repo: a CODEOWNERS file plus the touched files
    it names -- no git required (index_server.engine.ownership falls
    back to file-mtime-based signal when the repo isn't a git repo)."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "CODEOWNERS").write_text(
        "billing/*.py @billing-team\n"
        "billing/util.py @billing-team @jane-doe\n"
        "reporting/*.py @reporting-team\n"
    )
    (root / "billing").mkdir()
    (root / "billing" / "service.py").write_text("x = 1\n")
    (root / "billing" / "util.py").write_text("y = 2\n")
    (root / "reporting").mkdir()
    (root / "reporting" / "report.py").write_text("z = 3\n")
    (root / "unowned").mkdir()
    (root / "unowned" / "scratch.py").write_text("w = 4\n")
    return root
