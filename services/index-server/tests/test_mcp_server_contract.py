"""Wire-level contract test: spawns the REAL server.py as a subprocess
and talks MCP to it over stdio, exactly the way F3's own contract-test
template (services/mcp-stubs/tests/test_index_contract.py) does for the
stub. Proves this server advertises the exact same tool schemas F3
fixed, and that real (not canned) responses still conform to F3's own
output_schema for every named outcome.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
import pytest
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, ListToolsResult

from index_server.contract import load_index_schema

SERVER_SCRIPT = Path(__file__).resolve().parent.parent / "src" / "index_server" / "server.py"
SCHEMA = load_index_schema()


async def _list_tools_async() -> ListToolsResult:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.list_tools()


async def _call_tool_async(tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER_SCRIPT)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


def list_tools() -> ListToolsResult:
    return anyio.run(_list_tools_async)


def call_tool(tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
    return anyio.run(_call_tool_async, tool_name, arguments)


EXPECTED_TOOLS = {
    "find-definition",
    "find-references",
    "find-callers",
    "search",
    "get-file-module-summary",
    "get-ownership-metadata",
}


def test_advertised_tools_match_f3_schema_exactly():
    tools = list_tools()
    assert {t.name for t in tools.tools} == EXPECTED_TOOLS
    for t in tools.tools:
        assert t.input_schema == SCHEMA["tools"][t.name]["input_schema"]
        assert t.output_schema == SCHEMA["tools"][t.name]["output_schema"]


def test_real_ok_response_conforms_to_schema():
    result = call_tool(
        "find-references",
        {
            "tenant_id": "tenant-acme",
            "repository": "fixture/multi-pkg-repo",
            "symbol": "compute_total",
            "origin": {"file": "pkg_a/billing/util.py", "line": 7},
        },
    )
    payload = result.structured_content
    assert payload is not None
    errors = list(Draft202012Validator(SCHEMA["tools"]["find-references"]["output_schema"]).iter_errors(payload))
    assert not errors
    assert payload["outcome"] == "ok"
    assert payload["data"]["exhaustive"] is True
    assert payload["data"]["total_count"] == len(payload["data"]["references"])
    assert result.is_error is False


def test_real_empty_response_conforms_to_schema():
    result = call_tool(
        "find-references",
        {
            "tenant_id": "tenant-acme",
            "repository": "fixture/multi-pkg-repo",
            "symbol": "this_symbol_does_not_exist_anywhere_xyz",
        },
    )
    payload = result.structured_content
    errors = list(Draft202012Validator(SCHEMA["tools"]["find-references"]["output_schema"]).iter_errors(payload))
    assert not errors
    assert payload["outcome"] == "empty"
    assert "error" not in payload
    assert result.is_error is False


def test_real_error_response_conforms_to_schema():
    result = call_tool(
        "find-references",
        {
            "tenant_id": "tenant-unknown-to-this-deployment",
            "repository": "fixture/multi-pkg-repo",
            "symbol": "compute_total",
        },
    )
    payload = result.structured_content
    errors = list(Draft202012Validator(SCHEMA["tools"]["find-references"]["output_schema"]).iter_errors(payload))
    assert not errors
    assert payload["outcome"] == "error"
    assert payload["error"]["code"] == "permission-denied"
    assert "data" not in payload
    assert result.is_error is True


def test_search_tool_over_the_wire_is_labeled_non_authoritative():
    result = call_tool("search", {"tenant_id": "tenant-acme", "query": "compute total tax"})
    payload = result.structured_content
    errors = list(Draft202012Validator(SCHEMA["tools"]["search"]["output_schema"]).iter_errors(payload))
    assert not errors
    assert payload["outcome"] == "ok"
    assert payload["data"]["authoritative"] is False
    assert payload["data"]["label"] == "non-authoritative"


def test_malformed_input_is_rejected_before_reaching_the_service():
    result = call_tool("find-references", {"repository": "fixture/multi-pkg-repo", "symbol": "compute_total"})
    assert result.is_error is True
    assert "tenant_id" in result.content[0].text or "Malformed" in result.content[0].text
