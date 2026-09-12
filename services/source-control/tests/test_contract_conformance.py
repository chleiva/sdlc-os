"""Proves SourceControlService's real output is byte-for-byte conformant
with F3's published contract
(services/mcp-stubs/source-control/schema/source-control.schema.json) --
the same schema file the mcp-stubs source-control stub server is
validated against, so D5's real implementation can never quietly drift
from the interface D1-D4/D7 built against.

This is the "contract test D2/D3/D4/D5/D7 can run against the stub"
pattern from F3's own acceptance criteria, pointed at the real
implementation instead of the stub.
"""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from source_control.audit import AuditLogger
from source_control.service import InstallationRegistry, SourceControlService, TenantInstallation

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "mcp-stubs" / "source-control" / "schema" / "source-control.schema.json"
)
SCHEMA = json.loads(SCHEMA_PATH.read_text())
TOOLS = SCHEMA["tools"]

TENANT = "tenant-acme"
REPO = "acme/app"


def _validate(tool_name: str, payload: dict) -> None:
    output_schema = TOOLS[tool_name]["output_schema"]
    errors = sorted(Draft202012Validator(output_schema).iter_errors(payload), key=str)
    assert errors == [], f"{tool_name} output does not conform to F3 schema: {[e.message for e in errors]}"


def test_schema_file_is_the_one_this_test_thinks_it_is():
    assert SCHEMA["server"] == "source-control"
    assert set(TOOLS) == {
        "create-branch-worktree", "open-pr", "get-pr-check-status", "get-file-contents", "list-files",
    }


def test_create_branch_worktree_ok_and_error_conform(git_fixture_repo, tmp_path, rsa_keypair):
    import shutil

    _, _, private_pem = rsa_keypair
    mirror_root = tmp_path / "mirrors"
    dest = mirror_root / "acme" / "app"
    dest.parent.mkdir(parents=True)
    shutil.copytree(git_fixture_repo, dest)
    registry = InstallationRegistry()
    registry.register(TenantInstallation(
        tenant_id=TENANT, installation_id="inst-1", app_id="918273", app_slug="sdlc-auto",
        private_key_pem=private_pem, allowed_repositories=frozenset({REPO}), mirror_root=mirror_root,
    ))
    service = SourceControlService(registry, audit_logger=AuditLogger())

    ok = service.create_branch_worktree(
        tenant_id=TENANT, repository=REPO, base_ref="main", branch_name="feature/x", session_id="s1",
    )
    _validate("create-branch-worktree", ok)
    assert ok["outcome"] == "ok"

    empty = service.create_branch_worktree(
        tenant_id=TENANT, repository=REPO, base_ref="main", branch_name="feature/x", session_id="s2",
    )
    _validate("create-branch-worktree", empty)
    assert empty["outcome"] == "empty"

    error = service.create_branch_worktree(
        tenant_id="unknown-tenant", repository=REPO, base_ref="main", branch_name="feature/y", session_id="s3",
    )
    _validate("create-branch-worktree", error)
    assert error["outcome"] == "error"


def test_open_pr_and_check_status_and_file_tools_conform(mock_github, rsa_keypair, tmp_path):
    _, _, private_pem = rsa_keypair
    server, base_url = mock_github
    registry = InstallationRegistry()
    registry.register(TenantInstallation(
        tenant_id=TENANT, installation_id="inst-1", app_id="918273", app_slug="sdlc-auto",
        private_key_pem=private_pem, allowed_repositories=frozenset({REPO}),
        mirror_root=tmp_path / "mirrors", api_base_url=base_url,
    ))
    service = SourceControlService(registry, audit_logger=AuditLogger())

    server.state.refs[("acme", "app", "feature-1")] = "a" * 40
    pr_ok = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-1", base_branch="main",
        title="t", description="d",
    )
    _validate("open-pr", pr_ok)

    pr_empty = service.open_pr(
        tenant_id=TENANT, repository=REPO, head_branch="feature-1", base_branch="main",
        title="t2", description="d2",
    )
    _validate("open-pr", pr_empty)

    pr_error = service.open_pr(
        tenant_id="unknown", repository=REPO, head_branch="feature-1", base_branch="main",
        title="t", description="d",
    )
    _validate("open-pr", pr_error)

    status_empty = service.get_pr_check_status(tenant_id=TENANT, repository=REPO, pr_number=pr_ok["data"]["pr_number"])
    _validate("get-pr-check-status", status_empty)

    server.state.check_runs[("acme", "app", "a" * 40)] = [
        {"name": "unit-tests", "status": "completed", "conclusion": "success", "html_url": "https://x"},
    ]
    status_ok = service.get_pr_check_status(tenant_id=TENANT, repository=REPO, pr_number=pr_ok["data"]["pr_number"])
    _validate("get-pr-check-status", status_ok)

    status_error = service.get_pr_check_status(tenant_id=TENANT, repository=REPO, pr_number=99999)
    _validate("get-pr-check-status", status_error)

    server.state.contents[("acme", "app", "main", "f.py")] = {
        "path": "f.py", "sha": "abc", "size": 5, "encoding": "base64", "content": "aGVsbG8=\n",
    }
    contents_ok = service.get_file_contents(tenant_id=TENANT, repository=REPO, branch="main", path="f.py")
    _validate("get-file-contents", contents_ok)

    contents_error = service.get_file_contents(tenant_id=TENANT, repository=REPO, branch="main", path="missing.py")
    _validate("get-file-contents", contents_error)

    server.state.refs[("acme", "app", "main")] = "b" * 40
    server.state.trees[("acme", "app", "b" * 40)] = [{"path": "f.py", "type": "blob", "size": 5}]
    files_ok = service.list_files(tenant_id=TENANT, repository=REPO, branch="main")
    _validate("list-files", files_ok)

    files_empty = service.list_files(tenant_id=TENANT, repository=REPO, branch="main", path_prefix="nope/")
    _validate("list-files", files_empty)

    files_error = service.list_files(tenant_id=TENANT, repository=REPO, branch="ghost")
    _validate("list-files", files_error)
