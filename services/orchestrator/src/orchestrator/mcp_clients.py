"""Real MCP stdio clients against F3's stub servers.

Per the D2 brief: "build/test against F3's stubs first, don't block on
the real servers" -- swapping in D3/D4/D5's real MCP servers later is a
matter of pointing `StubServerClient` at a different launch command, not
changing anything above this module. This intentionally uses the same
technique F3's own contract tests use (`services/mcp-stubs/tests/_helpers.py`):
a real subprocess speaking MCP over stdio, not an in-process shortcut --
so a passing test here proves the wire contract, not just a Python call
graph.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_STUBS_ROOT = Path(__file__).resolve().parents[4] / "services" / "mcp-stubs"


def stub_server_script(server_name: str) -> str:
    path = MCP_STUBS_ROOT / server_name / "server.py"
    if not path.exists():
        raise FileNotFoundError(
            f"F3 stub server script not found at {path} -- expected services/mcp-stubs/{server_name}/server.py"
        )
    return str(path)


class StubServerClient:
    """One F3 MCP stub server, called over real stdio. A fresh subprocess
    per `call` (mirroring the F3 contract-test helper) keeps every call
    hermetic at a modest speed cost -- acceptable for an orchestration
    layer's own test suite, not a hot loop."""

    def __init__(self, server_script: str) -> None:
        self._server_script = server_script

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return anyio.run(self._call_async, tool_name, arguments)

    async def _call_async(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        params = StdioServerParameters(command=sys.executable, args=[self._server_script])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                if result.structured_content is None:
                    raise RuntimeError(f"stub server returned no structured_content for tool '{tool_name}'")
                return result.structured_content


def index_client() -> StubServerClient:
    return StubServerClient(stub_server_script("index"))


def issue_tracker_client() -> StubServerClient:
    return StubServerClient(stub_server_script("issue-tracker"))


def source_control_client() -> StubServerClient:
    return StubServerClient(stub_server_script("source-control"))


def ci_client() -> StubServerClient:
    return StubServerClient(stub_server_script("ci"))


def raise_on_error_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    """Section 7.6's three-way outcome discriminator: turn a named error
    result into a Python exception at the tool-invoker boundary, so
    orchestrator logic downstream only ever sees 'ok' data or 'empty' --
    it does not have to re-check `outcome` at every call site."""
    if payload.get("outcome") == "error":
        err = payload["error"]
        raise RuntimeError(f"MCP tool call failed: {err['code']}: {err['message']}")
    return payload
