"""Confluence research via the official Atlassian Rovo MCP server
(master spec Sec. 15, Sec. 4.4's pointer, Sec. 25.5).

    "Where the organization uses Confluence, research reads it through
    the official Atlassian Rovo MCP server rather than a bespoke
    scraper: it is Atlassian's own remote MCP endpoint for Jira and
    Confluence, authenticated via OAuth and scoped to exactly what the
    signed-in integration is permitted to see."

Rovo is Atlassian's own remote MCP server (not something D4 hosts), so
"implementing this for real" means: a real MCP client speaking the real
transport (Streamable HTTP: JSON-RPC 2.0 over a single HTTPS endpoint,
OAuth 2.0 Bearer-authenticated) that Atlassian's server implements --
not a bespoke scraper or a hand-rolled Confluence REST client, which is
exactly what Sec. 15 rules out. There is no live Rovo endpoint in this
environment, so this client is exercised against
`mocks/rovo_mock_server.py`, which speaks the same JSON-RPC/tool-call
shape.

ASSUMPTION FLAGGED FOR A HUMAN (see SETUP.md): the exact tool names and
argument shapes Atlassian's Rovo MCP server exposes are not fully
documented in this environment's available references. This client
follows the common `search` / `fetch` tool-naming convention used by
several remote MCP servers (search: query -> ranked candidate
resource ids; fetch: resource id -> full content) as its best-effort
target shape, scoped to Confluence by passing a `source: "confluence"`
argument. A human wiring this up against a real Rovo endpoint must
confirm the actual tool names/schemas from Atlassian's current
developer docs and adjust `_TOOL_SEARCH`/`_TOOL_FETCH` and the
argument/result mapping below accordingly -- the typed
`RovoSearchResult`/`RovoPage`/error-condition surface this module
exposes to the rest of D4 is intended to stay stable even if that
adjustment is needed.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import requests

from issue_tracker.errors import IssueTrackerError, from_http_response
from issue_tracker.result import Result

_TOOL_SEARCH = "search"
_TOOL_FETCH = "fetch"

_id_counter = itertools.count(1)


@dataclass
class RovoConfig:
    tenant_id: str
    base_url: str  # Atlassian's remote MCP endpoint, e.g. "https://mcp.atlassian.com/v2"
    oauth_bearer_token: str
    request_timeout_seconds: float = 10.0


@dataclass
class RovoClient:
    config: RovoConfig
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()

    def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One JSON-RPC 2.0 `tools/call` request over MCP's Streamable
        HTTP transport -- the same wire shape used by every remote MCP
        server, Rovo included."""
        request_id = next(_id_counter)
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        try:
            resp = self.session.post(
                f"{self.config.base_url.rstrip('/')}/mcp",
                json=body,
                headers={
                    "Authorization": f"Bearer {self.config.oauth_bearer_token}",
                    "Content-Type": "application/json",
                },
                timeout=self.config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            from issue_tracker.errors import UpstreamUnavailableError

            raise UpstreamUnavailableError(f"Could not reach Rovo MCP endpoint: {exc}") from exc

        if resp.status_code >= 400:
            raise from_http_response(resp.status_code, resp.text, resp.headers)

        envelope = resp.json()
        if "error" in envelope:
            # JSON-RPC-level error (distinct from a tool-level error
            # payload inside a successful JSON-RPC response).
            rpc_error = envelope["error"]
            from issue_tracker.errors import UpstreamUnavailableError

            raise UpstreamUnavailableError(f"Rovo MCP JSON-RPC error {rpc_error.get('code')}: {rpc_error.get('message')}")

        result = envelope.get("result", {})
        if result.get("isError"):
            structured = result.get("structuredContent") or {}
            code = structured.get("code", "upstream-unavailable")
            message = structured.get("message", "Rovo tool call reported an error.")
            from issue_tracker.errors import (
                NotFoundError,
                PermissionDeniedError,
                RateLimitedError,
                UpstreamUnavailableError,
            )

            error_types: dict[str, type[IssueTrackerError]] = {
                "not-found": NotFoundError,
                "permission-denied": PermissionDeniedError,
                "rate-limited": RateLimitedError,
                "upstream-unavailable": UpstreamUnavailableError,
            }
            raise error_types.get(code, UpstreamUnavailableError)(message)

        return result.get("structuredContent", {})

    def search_confluence(self, *, query: str, cql: str | None = None, limit: int = 10) -> dict[str, Any]:
        """Ranked candidate Confluence pages for a natural-language (or
        CQL) query -- explicitly non-authoritative, mirroring the
        index server's own `search` tool discipline (F3): never
        returned or treated as if it were an exhaustive/authoritative
        result.
        """
        try:
            arguments: dict[str, Any] = {"query": query, "source": "confluence", "limit": limit}
            if cql:
                arguments["cql"] = cql
            structured = self._call_tool(_TOOL_SEARCH, arguments)
            results = structured.get("results", [])
            if not results:
                return Result.empty(f"No Confluence pages matched query {query!r}.").to_wire()
            return Result.ok({
                "results": [
                    {
                        "page_id": r["id"],
                        "title": r.get("title", ""),
                        "space_key": r.get("space_key", ""),
                        "url": r.get("url", ""),
                        "excerpt": r.get("excerpt", ""),
                    }
                    for r in results
                ],
                "non_authoritative": True,
            }).to_wire()
        except IssueTrackerError as exc:
            return Result.fail(exc.code, exc.message, retry_after_seconds=exc.retry_after_seconds,
                                details=exc.details).to_wire()

    def get_page(self, *, page_id: str) -> dict[str, Any]:
        """Full content of one Confluence page by id, for a research
        step that already knows exactly which page it wants (e.g.
        following up on a `search_confluence` candidate)."""
        try:
            structured = self._call_tool(_TOOL_FETCH, {"id": page_id, "source": "confluence"})
            if not structured:
                return Result.empty(f"Confluence page {page_id!r} could not be fetched (no content).").to_wire()
            return Result.ok({
                "page_id": page_id,
                "title": structured.get("title", ""),
                "space_key": structured.get("space_key", ""),
                "url": structured.get("url", ""),
                "body_text": structured.get("text", ""),
            }).to_wire()
        except IssueTrackerError as exc:
            return Result.fail(exc.code, exc.message, retry_after_seconds=exc.retry_after_seconds,
                                details=exc.details).to_wire()
