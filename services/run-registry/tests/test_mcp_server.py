"""Smoke test for the MCP tool-registration layer itself (not just the
underlying RegistryService): confirms every minimum operation is exposed
as a named MCP tool, and that a round trip through a tool function
produces the same contract-shaped envelope as the service layer."""

from __future__ import annotations

import asyncio

import pytest

from run_registry.mcp_server import build_server

EXPECTED_TOOLS = {
    "create_run",
    "get_run",
    "list_runs",
    "list_attempts",
    "append_attempt",
    "transition_stage",
    "write_checkpoint",
    "write_execution_location",
}


@pytest.fixture
def mcp(tmp_path):
    server = build_server(str(tmp_path / "mcp.db"))
    return server


def test_all_minimum_operations_are_registered_tools(mcp):
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names


def test_create_and_get_run_round_trip_through_mcp_tool_call(mcp):
    create_result = asyncio.run(
        mcp.call_tool(
            "create_run",
            {
                "tenant_id": "tenant-mcp-1",
                "jira_key": "PROJ-1",
                "repo": "org/repo",
                "branch": "main",
                "capacity_class": "spot",
                "trace_id": "trace-1",
            },
        )
    )
    assert create_result.is_error is False
    import json

    payload = json.loads(create_result.content[0].text)
    assert payload["outcome"] == "ok"
    run_id = payload["data"]["id"]
    assert payload["data"]["stage"] == "intake"

    get_result = asyncio.run(
        mcp.call_tool("get_run", {"tenant_id": "tenant-mcp-1", "run_id": run_id})
    )
    get_payload = json.loads(get_result.content[0].text)
    assert get_payload["outcome"] == "ok"
    assert get_payload["data"]["id"] == run_id

    # Wrong tenant via the MCP tool call must also fail closed.
    wrong_tenant = asyncio.run(
        mcp.call_tool("get_run", {"tenant_id": "someone-else", "run_id": run_id})
    )
    wrong_payload = json.loads(wrong_tenant.content[0].text)
    assert wrong_payload["outcome"] == "empty"
