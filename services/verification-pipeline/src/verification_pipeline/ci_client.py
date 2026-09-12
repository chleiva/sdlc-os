"""Real MCP client for F3's CI contract (services/mcp-stubs/ci).

This talks actual MCP-over-stdio to F3's stub server -- the same pattern
services/mcp-stubs/tests/_helpers.py uses to prove another deliverable is
calling correctly. D7 does not re-implement test execution (that's the
org's own CI, Sec. 15); this client only triggers a run and polls for its
result, exactly the two tools ci.schema.json defines.

A live CI system isn't available in this environment -- expected, per the
brief -- so this is exercised against F3's real stub server process, not
an in-process shortcut and not hand-copied canned data.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MCP_STUBS_ROOT = Path(__file__).resolve().parents[3] / "mcp-stubs"
CI_SERVER_SCRIPT = MCP_STUBS_ROOT / "ci" / "server.py"


class CIClientError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class CIClient:
    """Thin, real MCP client for the `ci` server. One instance per call by
    default (matches the stub test harness's hermetic-subprocess-per-call
    pattern) -- stub servers are cheap to spawn and this keeps every call
    independent, with no shared, possibly-stateful connection.
    """

    def __init__(self, server_script: Path | None = None, python_executable: str | None = None):
        self.server_script = server_script or CI_SERVER_SCRIPT
        self.python_executable = python_executable or sys.executable

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        params = StdioServerParameters(command=self.python_executable, args=[str(self.server_script)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
        payload = result.structured_content
        if payload is None:
            raise CIClientError("upstream-unavailable", f"{tool_name}: no structured_content in response")
        return payload

    def trigger_run(self, tenant_id: str, repository: str, ref: str, scope: list[str], run_type: str = "test") -> dict:
        args = {"tenant_id": tenant_id, "repository": repository, "ref": ref, "scope": scope, "run_type": run_type}
        return anyio.run(self._call, "trigger-run", args)

    def get_run_status_result(self, tenant_id: str, repository: str, run_id: str) -> dict:
        args = {"tenant_id": tenant_id, "repository": repository, "run_id": run_id}
        return anyio.run(self._call, "get-run-status-result", args)
