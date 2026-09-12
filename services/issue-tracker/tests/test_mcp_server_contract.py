"""Contract test: the real MCP server (`mcp_server.py`, backed by
`JiraClient` against the mock) advertises exactly F3's schema tools and
every response it produces validates against that schema's own
output_schema -- the same discipline F3's stub servers are held to,
now proven against a real (mock-backed) implementation instead of
canned data.
"""

import asyncio

import mcp.types as types
import pytest
from jsonschema import Draft202012Validator

from issue_tracker import mcp_server
from issue_tracker.schema_loader import load_schema

SCHEMA = load_schema(mcp_server.SCHEMA_PATH)


def _run(coro):
    return asyncio.run(coro)


def test_schema_file_is_reachable_and_matches_f3_tool_names():
    assert set(SCHEMA["tools"]) == set(mcp_server.TOOL_FNS)


def _call_tool(server, name: str, arguments: dict):
    entry = server.get_request_handler("tools/call")
    params = types.CallToolRequestParams(name=name, arguments=arguments)
    return _run(entry.handler(None, params))


def test_list_tools_matches_schema(jira_client):
    server = mcp_server.build_server(client_for_tenant=lambda tenant_id: jira_client)
    entry = server.get_request_handler("tools/list")
    result = _run(entry.handler(None, types.PaginatedRequestParams()))
    names = {t.name for t in result.tools}
    assert names == set(SCHEMA["tools"])


def test_create_epic_through_real_mcp_server_matches_output_schema(jira_client):
    server = mcp_server.build_server(client_for_tenant=lambda tenant_id: jira_client)
    result = _call_tool(server, "create-epic", {
        "tenant_id": "tenant-acme",
        "project_key": "PROJ",
        "summary": "MCP contract epic",
        "description": "d",
        "acceptance_criteria": ["ac1"],
    })
    assert result.structured_content["outcome"] == "ok"
    Draft202012Validator(SCHEMA["tools"]["create-epic"]["output_schema"]).validate(result.structured_content)


def test_malformed_input_is_rejected_before_touching_jira(jira_client):
    server = mcp_server.build_server(client_for_tenant=lambda tenant_id: jira_client)
    result = _call_tool(server, "create-epic", {"tenant_id": "tenant-acme"})
    assert result.is_error
