"""The real issue-tracker MCP server: F3's schema, backed by
`jira_client.JiraClient` instead of the F3 stub's canned data.

This is the drop-in replacement for
`services/mcp-stubs/issue-tracker/server.py` once a tenant's real Jira
credentials are configured -- same schema file (F3 is the single
source of truth for the wire contract; this module never redefines it),
same tool names, same oneOf(ok/empty/error) shape, but every response
comes from an actual (or, here, mocked-Jira-backed) `JiraClient` call
instead of a hardcoded scenario. Every outgoing payload is validated
against the schema's own output_schema before being returned, exactly
like the stub, so a bug in this real implementation fails loudly in
tests rather than silently shipping a non-conformant response.

This module intentionally does not import
`services/mcp-stubs/_common` (out of scope to modify/depend on
internals of that deliverable's package layout); it re-implements the
small amount of generic MCP wiring it needs directly against the `mcp`
package, using the exact same schema file F3 published as the single
source of truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import mcp.server.stdio as stdio
import mcp.types as types
from jsonschema import Draft202012Validator
from mcp.server.lowlevel import Server

from issue_tracker.jira_client import JiraClient
from issue_tracker.schema_loader import load_schema

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "mcp-stubs"
    / "issue-tracker"
    / "schema"
    / "issue-tracker.schema.json"
)

ToolFn = Callable[[JiraClient, dict[str, Any]], dict[str, Any]]


def _create_epic(client: JiraClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.create_epic(
        project_key=args["project_key"],
        summary=args["summary"],
        description=args["description"],
        acceptance_criteria=args["acceptance_criteria"],
        size=args.get("size"),
        labels=args.get("labels"),
    )


def _create_story(client: JiraClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.create_story(
        project_key=args["project_key"],
        epic_key=args["epic_key"],
        summary=args["summary"],
        description=args["description"],
        size=args["size"],
        depends_on=args.get("depends_on"),
        labels=args.get("labels"),
    )


def _get_issue(client: JiraClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.get_issue(issue_key=args["issue_key"])


def _transition_status(client: JiraClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.transition_status(
        issue_key=args["issue_key"], target_status=args["target_status"], comment=args.get("comment")
    )


def _post_comment(client: JiraClient, args: dict[str, Any]) -> dict[str, Any]:
    return client.post_comment(
        issue_key=args["issue_key"], body=args["body"], comment_type=args.get("comment_type", "general")
    )


TOOL_FNS: dict[str, ToolFn] = {
    "create-epic": _create_epic,
    "create-story": _create_story,
    "get-issue": _get_issue,
    "transition-status": _transition_status,
    "post-comment": _post_comment,
}


def build_server(*, client_for_tenant: Callable[[str], JiraClient], schema_path: Path = SCHEMA_PATH) -> Server:
    schema = load_schema(schema_path)
    tools_meta: dict[str, Any] = schema["tools"]

    missing = set(tools_meta) - set(TOOL_FNS)
    if missing:
        raise RuntimeError(f"issue-tracker (real): no implementation registered for tools: {sorted(missing)}")

    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=meta["summary"],
                    input_schema=meta["input_schema"],
                    output_schema=meta["output_schema"],
                )
                for name, meta in tools_meta.items()
            ]
        )

    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        tool_name = params.name
        args = dict(params.arguments or {})

        if tool_name not in tools_meta:
            payload = {"outcome": "error", "error": {"code": "not-found",
                       "message": f"Unknown tool '{tool_name}'.", "retryable": False, "retry_after_seconds": None}}
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(payload))],
                                         structured_content=payload, is_error=True)

        meta = tools_meta[tool_name]
        input_errors = sorted(Draft202012Validator(meta["input_schema"]).iter_errors(args), key=str)
        if input_errors:
            message = "; ".join(e.message for e in input_errors)
            text = f"Malformed input for '{tool_name}': {message}"
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)

        tenant_id = args.get("tenant_id", "")
        client = client_for_tenant(tenant_id)
        payload = TOOL_FNS[tool_name](client, args)

        output_errors = sorted(Draft202012Validator(meta["output_schema"]).iter_errors(payload), key=str)
        if output_errors:
            details = "; ".join(e.message for e in output_errors)
            raise AssertionError(f"issue-tracker (real) .{tool_name}: response does not match output_schema: {details}")

        return types.CallToolResult(
            content=[types.TextContent(type="text", text=json.dumps(payload, indent=2))],
            structured_content=payload,
            is_error=(payload["outcome"] == "error"),
        )

    return Server("issue-tracker", version=schema["version"], on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def run_stdio(server: Server) -> None:
    async with stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
