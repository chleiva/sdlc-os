"""A local mock HTTP server standing in for Atlassian's Rovo MCP
server (Confluence research, master spec Sec. 15), speaking the same
JSON-RPC 2.0 `tools/call` shape `rovo_client.py` sends over MCP's
Streamable HTTP transport.

There is no live Rovo/Confluence endpoint available in this
environment (same constraint as Jira) -- this mock exists so
`rovo_client.py`'s request-building and response-parsing code is
exercised against *something* real-shaped, per the same
mock-instead-of-live-account discipline used for Jira.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class RovoMockStore:
    pages: dict[str, dict[str, Any]] = field(default_factory=dict)
    require_bearer_token: str | None = None
    force_status: int | None = None

    def seed_page(self, page_id: str, *, title: str, space_key: str, url: str, text: str) -> None:
        self.pages[page_id] = {"id": page_id, "title": title, "space_key": space_key, "url": url, "text": text}


def make_handler(store: RovoMockStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def _send(self, status: int, obj: dict[str, Any]) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            if store.force_status is not None:
                self.send_response(store.force_status)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            if store.require_bearer_token is not None:
                auth = self.headers.get("Authorization", "")
                if auth != f"Bearer {store.require_bearer_token}":
                    self._send(401, {"errorMessages": ["invalid or missing OAuth bearer token"]})
                    return

            length = int(self.headers.get("Content-Length", "0"))
            envelope = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            if envelope.get("method") != "tools/call":
                self._send(400, {"jsonrpc": "2.0", "id": envelope.get("id"),
                                  "error": {"code": -32601, "message": "unsupported method"}})
                return

            params = envelope.get("params", {})
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            if tool_name == "search":
                structured = self._search(arguments)
            elif tool_name == "fetch":
                structured = self._fetch(arguments)
            else:
                self._send(200, {"jsonrpc": "2.0", "id": envelope.get("id"),
                                  "result": {"isError": True,
                                             "structuredContent": {"code": "not-found",
                                                                    "message": f"unknown tool {tool_name!r}"}}})
                return

            self._send(200, {"jsonrpc": "2.0", "id": envelope.get("id"),
                              "result": {"isError": False, "structuredContent": structured}})

        def _search(self, arguments: dict[str, Any]) -> dict[str, Any]:
            query = (arguments.get("query") or "").lower()
            matches = [p for p in store.pages.values() if query in p["title"].lower() or query in p["text"].lower()]
            return {
                "results": [
                    {"id": p["id"], "title": p["title"], "space_key": p["space_key"], "url": p["url"],
                     "excerpt": p["text"][:120]}
                    for p in matches[: arguments.get("limit", 10)]
                ]
            }

        def _fetch(self, arguments: dict[str, Any]) -> dict[str, Any]:
            page = store.pages.get(arguments.get("id"))
            if page is None:
                return {}
            return page

    return Handler


def start_mock_server(host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, RovoMockStore]:
    store = RovoMockStore()
    server = ThreadingHTTPServer((host, port), make_handler(store))
    return server, store
