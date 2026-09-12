#!/usr/bin/env python3
"""Real Repository Index MCP server (D3).

Runnable standalone:

    python3 services/index-server/src/index_server/server.py

Speaks MCP over stdio, exactly like services/mcp-stubs/index/server.py.
Every tool's advertised input/output schema comes verbatim from F3's own
schema/index.schema.json (imported via index_server.contract, never
hand-copied). Unlike the stub, the outcome for a given call is not
selected by tenant_id sentinel values -- it is the real result of
parsing, indexing, and querying whatever repository the tenant_id is
authorized for (config/tenants.json).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anyio
import mcp.server.stdio as stdio
import mcp.types as types
from jsonschema import Draft202012Validator
from mcp.server.lowlevel import Server

from index_server.contract import load_index_schema
from index_server.service import IndexService

SCHEMA = load_index_schema()

TOOL_METHODS = {
    "find-definition": "find_definition",
    "find-references": "find_references",
    "find-callers": "find_callers",
    "search": "search",
    "get-file-module-summary": "get_file_module_summary",
    "get-ownership-metadata": "get_ownership_metadata",
}


def build_server(service: IndexService, name: str = "index-server") -> Server:
    tools_meta: dict[str, Any] = SCHEMA["tools"]
    missing = set(tools_meta) - set(TOOL_METHODS)
    if missing:
        raise RuntimeError(f"{name}: no implementation registered for tools: {sorted(missing)}")

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool_name,
                    description=meta["summary"],
                    input_schema=meta["input_schema"],
                    output_schema=meta["output_schema"],
                )
                for tool_name, meta in tools_meta.items()
            ]
        )

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool_name = params.name
        args = dict(params.arguments or {})

        if tool_name not in tools_meta:
            payload = {
                "outcome": "error",
                "error": {"code": "not-found", "message": f"Unknown tool '{tool_name}'.", "retryable": False, "retry_after_seconds": None},
            }
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=json.dumps(payload))],
                structured_content=payload,
                is_error=True,
            )

        meta = tools_meta[tool_name]
        input_errors = sorted(Draft202012Validator(meta["input_schema"]).iter_errors(args), key=str)
        if input_errors:
            message = "; ".join(e.message for e in input_errors)
            text = f"Malformed input for '{tool_name}': {message}"
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)

        method = getattr(service, TOOL_METHODS[tool_name])
        payload = method(args)

        output_errors = sorted(Draft202012Validator(meta["output_schema"]).iter_errors(payload), key=str)
        if output_errors:
            details = "; ".join(e.message for e in output_errors)
            raise AssertionError(f"{name}.{tool_name}: response does not match its own output_schema: {details}")

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, indent=2))],
            structured_content=payload,
            is_error=(payload.get("outcome") == "error"),
        )

    return Server(name, version=SCHEMA["version"], on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def run_stdio(server: Server) -> None:
    async with stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


server = build_server(IndexService())

if __name__ == "__main__":
    anyio.run(run_stdio, server)
