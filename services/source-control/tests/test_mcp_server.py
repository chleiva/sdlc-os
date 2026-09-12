"""Smoke test for the real MCP tool-registration layer itself (not just
SourceControlService underneath it): confirms every F3 tool is exposed
under its exact contract name, and that a round trip through a tool call
produces the same contract-shaped envelope the schema requires."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from source_control.mcp_server import build_server
from source_control.service import InstallationRegistry, TenantInstallation

EXPECTED_TOOLS = {
    "create-branch-worktree", "open-pr", "get-pr-check-status", "get-file-contents", "list-files",
}

TENANT = "tenant-acme"
REPO = "acme/app"


@pytest.fixture
def mcp(git_fixture_repo: Path, tmp_path: Path, rsa_keypair, mock_github):
    _, _, private_pem = rsa_keypair
    _, base_url = mock_github
    mirror_root = tmp_path / "mirrors"
    dest = mirror_root / "acme" / "app"
    dest.parent.mkdir(parents=True)
    shutil.copytree(git_fixture_repo, dest)

    registry = InstallationRegistry()
    registry.register(TenantInstallation(
        tenant_id=TENANT, installation_id="inst-1", app_id="918273", app_slug="sdlc-auto",
        private_key_pem=private_pem, allowed_repositories=frozenset({REPO}),
        mirror_root=mirror_root, api_base_url=base_url,
    ))
    return build_server(registry)


def test_all_five_f3_tools_are_registered(mcp):
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names


def test_create_branch_worktree_round_trip_through_mcp_tool_call(mcp):
    result = asyncio.run(mcp.call_tool(
        "create-branch-worktree",
        {
            "tenant_id": TENANT, "repository": REPO, "base_ref": "main",
            "branch_name": "feature/mcp-1", "session_id": "session-mcp-1",
        },
    ))
    assert result.is_error is False
    payload = json.loads(result.content[0].text)
    assert payload["outcome"] == "ok"
    assert payload["data"]["branch_name"] == "feature/mcp-1"
    assert Path(payload["data"]["worktree_path"]).is_dir()


def test_wrong_tenant_via_mcp_tool_call_fails_closed_as_permission_denied(mcp):
    result = asyncio.run(mcp.call_tool(
        "create-branch-worktree",
        {
            "tenant_id": "someone-else", "repository": REPO, "base_ref": "main",
            "branch_name": "feature/x", "session_id": "session-x",
        },
    ))
    payload = json.loads(result.content[0].text)
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "permission-denied"
