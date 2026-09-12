"""SourceControlService's four GitHub-REST-backed tools (open-pr,
get-pr-check-status, get-file-contents, list-files), exercised against
the real HTTP request/response shapes served by mock_github_server.py --
same client code path that would hit api.github.com."""

from __future__ import annotations

import base64

import pytest

from source_control.audit import AuditLogger
from source_control.service import InstallationRegistry, SourceControlService, TenantInstallation

TENANT = "tenant-acme"
REPO = "acme/app"


@pytest.fixture
def installation(rsa_keypair, mock_github, tmp_path):
    _, _, private_pem = rsa_keypair
    _, base_url = mock_github
    return TenantInstallation(
        tenant_id=TENANT,
        installation_id="inst-42",
        app_id="918273",
        app_slug="sdlc-auto",
        private_key_pem=private_pem,
        allowed_repositories=frozenset({REPO}),
        mirror_root=tmp_path / "mirrors",
        api_base_url=base_url,
    )


@pytest.fixture
def registry(installation):
    reg = InstallationRegistry()
    reg.register(installation)
    return reg


@pytest.fixture
def service(registry):
    return SourceControlService(registry, audit_logger=AuditLogger())


def test_open_pr_success(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "feature-x")] = "b" * 40

    result = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-x", base_branch="main",
        title="Add feature X", description="Generated PR description.",
    )
    assert result["outcome"] == "ok"
    assert result["data"]["pr_number"] == 1
    assert result["data"]["state"] == "open"
    assert result["data"]["url"].endswith("/pull/1")


def test_open_pr_draft(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "feature-y")] = "c" * 40
    result = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-y", base_branch="main",
        title="Draft PR", description="wip", draft=True,
    )
    assert result["outcome"] == "ok"
    assert result["data"]["state"] == "draft"


def test_open_pr_empty_when_one_already_open(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "feature-dup")] = "d" * 40
    first = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-dup", base_branch="main",
        title="First", description="first",
    )
    assert first["outcome"] == "ok"

    second = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-dup", base_branch="main",
        title="Second attempt", description="second",
    )
    assert second["outcome"] == "empty"
    assert second["existing_pr_number"] == first["data"]["pr_number"]


def test_open_pr_permission_denied_for_unregistered_repo(service):
    result = service.open_pr(
        tenant_id=TENANT, repository="acme/other-repo", head_branch="x", base_branch="main",
        title="t", description="d",
    )
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "permission-denied"
    assert result["error"]["retryable"] is False


def test_open_pr_permission_denied_for_unknown_tenant(service):
    result = service.open_pr(
        tenant_id="tenant-unknown", repository=REPO, head_branch="x", base_branch="main",
        title="t", description="d",
    )
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "permission-denied"


def test_get_pr_check_status_success(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "feature-z")] = "e" * 40
    opened = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-z", base_branch="main",
        title="t", description="d",
    )
    pr_number = opened["data"]["pr_number"]
    sha = "e" * 40
    server.state.check_runs[("acme", "app", sha)] = [
        {"name": "unit-tests", "status": "completed", "conclusion": "success", "html_url": "https://ci.example.com/1"},
        {"name": "lint", "status": "in_progress", "conclusion": None, "html_url": "https://ci.example.com/2"},
    ]

    result = service.get_pr_check_status(tenant_id=TENANT, repository=REPO, pr_number=pr_number)
    assert result["outcome"] == "ok"
    assert result["data"]["pr_number"] == pr_number
    assert result["data"]["state"] == "open"
    assert result["data"]["mergeable"] is True
    names = {c["name"] for c in result["data"]["checks"]}
    assert names == {"unit-tests", "lint"}
    by_name = {c["name"]: c for c in result["data"]["checks"]}
    assert by_name["unit-tests"]["status"] == "completed"
    assert by_name["unit-tests"]["conclusion"] == "success"
    assert by_name["lint"]["status"] == "in_progress"
    assert by_name["lint"]["conclusion"] is None


def test_get_pr_check_status_empty_when_no_checks_registered(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "feature-nochecks")] = "f" * 40
    opened = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-nochecks", base_branch="main",
        title="t", description="d",
    )
    result = service.get_pr_check_status(tenant_id=TENANT, repository=REPO, pr_number=opened["data"]["pr_number"])
    assert result["outcome"] == "empty"
    assert "no checks" in result["reason"]


def test_get_pr_check_status_not_found_for_unknown_pr(service):
    result = service.get_pr_check_status(tenant_id=TENANT, repository=REPO, pr_number=9999)
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "not-found"
    assert result["error"]["retryable"] is False


def test_get_pr_check_status_maps_unusual_conclusions_into_the_schemas_closed_enum(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "feature-weird")] = "1" * 40
    opened = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-weird", base_branch="main",
        title="t", description="d",
    )
    server.state.check_runs[("acme", "app", "1" * 40)] = [
        {"name": "flaky", "status": "completed", "conclusion": "timed_out", "html_url": "https://x"},
        {"name": "skip-me", "status": "completed", "conclusion": "skipped", "html_url": "https://x"},
    ]
    result = service.get_pr_check_status(tenant_id=TENANT, repository=REPO, pr_number=opened["data"]["pr_number"])
    by_name = {c["name"]: c for c in result["data"]["checks"]}
    assert by_name["flaky"]["conclusion"] == "failure"
    assert by_name["skip-me"]["conclusion"] == "neutral"


def test_get_file_contents_success(service, mock_github):
    server, _ = mock_github
    content = b"print('hello world')\n"
    server.state.contents[("acme", "app", "main", "src/app.py")] = {
        "path": "src/app.py",
        "sha": "abc123",
        "size": len(content),
        "encoding": "base64",
        "content": base64.b64encode(content).decode("ascii") + "\n",
    }
    result = service.get_file_contents(tenant_id=TENANT, repository=REPO, branch="main", path="src/app.py")
    assert result["outcome"] == "ok"
    assert result["data"]["encoding"] == "base64"
    assert base64.b64decode(result["data"]["content"]) == content
    assert result["data"]["sha"] == "abc123"


def test_get_file_contents_empty_for_zero_byte_file(service, mock_github):
    server, _ = mock_github
    server.state.contents[("acme", "app", "main", "empty.txt")] = {
        "path": "empty.txt", "sha": "zzz", "size": 0, "encoding": "base64", "content": "",
    }
    result = service.get_file_contents(tenant_id=TENANT, repository=REPO, branch="main", path="empty.txt")
    assert result["outcome"] == "empty"
    assert "empty" in result["reason"]


def test_get_file_contents_not_found(service):
    result = service.get_file_contents(tenant_id=TENANT, repository=REPO, branch="main", path="nope.txt")
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "not-found"


def test_list_files_success(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "main")] = "9" * 40
    server.state.trees[("acme", "app", "9" * 40)] = [
        {"path": "src/main.py", "type": "blob", "size": 100},
        {"path": "src/utils.py", "type": "blob", "size": 50},
        {"path": "tests", "type": "tree", "size": 0},
    ]
    result = service.list_files(tenant_id=TENANT, repository=REPO, branch="main")
    assert result["outcome"] == "ok"
    paths = {f["path"]: f["type"] for f in result["data"]["files"]}
    assert paths == {"src/main.py": "file", "src/utils.py": "file", "tests": "directory"}


def test_list_files_respects_path_prefix(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "main")] = "8" * 40
    server.state.trees[("acme", "app", "8" * 40)] = [
        {"path": "src/main.py", "type": "blob", "size": 100},
        {"path": "docs/readme.md", "type": "blob", "size": 20},
    ]
    result = service.list_files(tenant_id=TENANT, repository=REPO, branch="main", path_prefix="src/")
    assert result["outcome"] == "ok"
    assert [f["path"] for f in result["data"]["files"]] == ["src/main.py"]


def test_list_files_empty_when_nothing_matches_prefix(service, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "main")] = "7" * 40
    server.state.trees[("acme", "app", "7" * 40)] = [{"path": "src/main.py", "type": "blob", "size": 1}]
    result = service.list_files(tenant_id=TENANT, repository=REPO, branch="main", path_prefix="nope/")
    assert result["outcome"] == "empty"


def test_list_files_not_found_for_unknown_branch(service):
    result = service.list_files(tenant_id=TENANT, repository=REPO, branch="ghost-branch")
    assert result["outcome"] == "error"
    assert result["error"]["code"] == "not-found"
