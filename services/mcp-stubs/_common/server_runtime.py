"""Generic low-level MCP server wiring shared by all four stub servers.

Each server module (e.g. services/mcp-stubs/index/server.py) does exactly
two things: load its schema/<name>.schema.json file, and supply a
ToolImpl (a pair of canned-data builder functions) per tool name. This
module does the rest -- advertising tools whose name/description/
input_schema/output_schema come verbatim from the schema file (so the
schema file is the single source of truth the running server can never
drift from), dispatching the tenant_id-driven scenario, and validating
every outgoing payload against the tool's own output_schema before it is
returned, so a bug in a stub's canned data fails loudly in-process rather
than silently shipping a non-conformant response to a caller under test.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import mcp.server.stdio as stdio
import mcp.types as types
from jsonschema import Draft202012Validator
from mcp.server.lowlevel import Server

from . import envelope
from .scenarios import scenario_for

DataBuilder = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class ToolImpl:
    """Canned-response builders for one tool.

    success_data(args) -> the 'data' object for the outcome == 'ok' branch.
    empty(args)        -> {'reason': str, **any extra fields the tool's
                            EmptyResult schema requires} for the
                            outcome == 'empty' branch.
    Error-branch payloads need no per-tool builder: they are identical in
    shape across every tool (see _common/envelope.py), so the runtime
    below builds them directly from the scenario name.
    """

    success_data: DataBuilder
    empty: DataBuilder


def build_server(name: str, version: str, schema_doc: dict[str, Any], impls: dict[str, ToolImpl]) -> Server:
    tools_meta: dict[str, Any] = schema_doc["tools"]
    missing = set(tools_meta) - set(impls)
    if missing:
        raise RuntimeError(f"{name}: no canned-response implementation registered for tools: {sorted(missing)}")
    extra = set(impls) - set(tools_meta)
    if extra:
        raise RuntimeError(f"{name}: implementation registered for tools not in the schema: {sorted(extra)}")

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
            payload = envelope.error("not-found", message=f"Unknown tool '{tool_name}' on server '{name}'.")
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
            # Schema-invalid input is a protocol-level validation failure,
            # not one of the four named domain error conditions -- it is
            # surfaced as a plain tool error, never disguised as
            # not-found/permission-denied/etc.
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)

        tenant_id = args.get("tenant_id", "")
        scenario = scenario_for(tenant_id)
        impl = impls[tool_name]

        if scenario == "ok":
            payload = envelope.ok(impl.success_data(args))
        elif scenario == "empty":
            fields = dict(impl.empty(args))
            reason = fields.pop("reason")
            payload = envelope.empty(reason, **fields)
        else:
            payload = envelope.error(scenario)

        output_errors = sorted(Draft202012Validator(meta["output_schema"]).iter_errors(payload), key=str)
        if output_errors:
            # A bug in this stub's own canned data, not a caller error.
            # Fail loudly rather than shipping a non-conformant response.
            details = "; ".join(e.message for e in output_errors)
            raise AssertionError(
                f"{name}.{tool_name}: canned '{scenario}' response does not match its own output_schema: {details}"
            )

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, indent=2))],
            structured_content=payload,
            is_error=(scenario not in ("ok", "empty")),
        )

    return Server(name, version=version, on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def run_stdio(server: Server) -> None:
    async with stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
