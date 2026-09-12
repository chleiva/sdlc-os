"""Shared test helpers -- not a test module itself (no test_ prefix).

Spawns a stub server as a real subprocess and talks MCP to it over stdio,
exactly the way another deliverable's own MCP client would. There is no
in-process shortcut here on purpose: these tests are meant to prove the
wire contract, not just the Python call graph.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import anyio
from jsonschema import Draft202012Validator
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, ListToolsResult

STUBS_ROOT = Path(__file__).resolve().parent.parent


def server_script(server_name: str) -> str:
    return str(STUBS_ROOT / server_name / "server.py")


def schema_doc(server_name: str) -> dict[str, Any]:
    path = STUBS_ROOT / server_name / "schema" / f"{server_name}.schema.json"
    return json.loads(path.read_text())


async def _list_tools_async(server_name: str) -> ListToolsResult:
    params = StdioServerParameters(command=sys.executable, args=[server_script(server_name)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.list_tools()


def list_tools(server_name: str) -> ListToolsResult:
    return anyio.run(_list_tools_async, server_name)


async def _call_tool_async(server_name: str, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
    params = StdioServerParameters(command=sys.executable, args=[server_script(server_name)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool_name, arguments)


def call_tool(server_name: str, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
    """Spawn the given stub server fresh and call one tool on it.

    A fresh subprocess per call keeps every test hermetic (no shared,
    possibly-stateful connection across assertions) at the cost of some
    speed; fine for a contract-test suite that is meant to run in CI, not
    in a hot loop.
    """
    return anyio.run(_call_tool_async, server_name, tool_name, arguments)


def assert_matches_contract(
    result: CallToolResult,
    tool_meta: dict[str, Any],
    expected_scenario: str,
) -> dict[str, Any]:
    """Assert a CallToolResult conforms to its tool's own output_schema and
    to the scenario it was supposed to produce. Returns structured_content
    for any further assertion the caller wants to make.

    This is the check another deliverable's agent is meant to run against
    these stubs to prove it is calling correctly: same schema file, same
    outcome discriminator, same four named error codes, every time.
    """
    payload = result.structured_content
    assert payload is not None, "stub did not return structured_content"

    errors = list(Draft202012Validator(tool_meta["output_schema"]).iter_errors(payload))
    assert not errors, "response does not match its own output_schema: " + "; ".join(e.message for e in errors)

    if expected_scenario == "ok":
        assert payload["outcome"] == "ok"
        assert "data" in payload
        assert "error" not in payload
        assert result.is_error is False
    elif expected_scenario == "empty":
        assert payload["outcome"] == "empty"
        assert "reason" in payload and payload["reason"]
        assert "data" not in payload
        assert "error" not in payload
        assert result.is_error is False
    else:
        # One of the four named error conditions.
        assert payload["outcome"] == "error"
        assert payload["error"]["code"] == expected_scenario
        assert "data" not in payload
        assert result.is_error is True

    return payload

