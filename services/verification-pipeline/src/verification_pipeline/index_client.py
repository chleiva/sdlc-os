"""Real MCP client for D3's index server (services/index-server).

Layer 7 (the exhaustive cross-codebase completion check, Sec. 6.6) calls
D3's actual, already-built MCP server -- not a stub, not an in-process
re-implementation -- exactly as the D7 brief requires. This is the same
stdio MCP transport services/mcp-stubs/tests/_helpers.py and D7's own
ci_client.py use, pointed at D3's real server.py instead of a canned
stub.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

INDEX_SERVER_ROOT = Path(__file__).resolve().parents[3] / "index-server"
INDEX_SERVER_SCRIPT = INDEX_SERVER_ROOT / "src" / "index_server" / "server.py"


class IndexClientError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class IndexClient:
    """Real MCP client for D3's index server. Every call spawns D3's
    actual server.py as a subprocess of *this repo's* verification-pipeline
    virtualenv (which carries the same mcp/jsonschema/anyio/tree-sitter
    pins D3's own pyproject.toml declares), talks MCP over stdio, and
    returns the tool's real structured_content.
    """

    def __init__(self, server_script: Path | None = None, python_executable: str | None = None):
        self.server_script = server_script or INDEX_SERVER_SCRIPT
        self.python_executable = python_executable or sys.executable
        if not self.server_script.is_file():
            raise FileNotFoundError(
                f"D3's index server script not found at {self.server_script}; "
                "is services/index-server checked out at the expected path?"
            )

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        params = StdioServerParameters(command=self.python_executable, args=[str(self.server_script)])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
        payload = result.structured_content
        if payload is None:
            raise IndexClientError("upstream-unavailable", f"{tool_name}: no structured_content in response")
        return payload

    def find_references(
        self,
        tenant_id: str,
        repository: str,
        symbol: str,
        origin: dict[str, Any] | None = None,
        include_test_files: bool = True,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "tenant_id": tenant_id,
            "repository": repository,
            "symbol": symbol,
            "include_test_files": include_test_files,
        }
        if origin is not None:
            args["origin"] = origin
        return anyio.run(self._call, "find-references", args)
