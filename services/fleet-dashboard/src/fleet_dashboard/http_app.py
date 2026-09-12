"""Minimal stdlib-only HTTP server for the Fleet Control Dashboard.

Deliberately dependency-light (brief: "no heavy build toolchain
requirement to just view it") -- this uses only `http.server` from the
standard library, no web framework. Two things it serves:

  * `GET /api/cards` -- the JSON poll response (`DashboardService.poll`),
    tenant-scoped from the caller's identity (never a client-supplied
    tenant_id trusted on its own -- see `auth.py` / `_resolve_scope`).
  * `GET /` -- the static single-file Kanban UI (`static/index.html`),
    which polls `/api/cards` on a fixed interval
    (`config.POLL_INTERVAL_SECONDS`) via `fetch`.

This module contains no business logic of its own -- it parses the
request, resolves identity/scope/filters, and delegates to
`DashboardService`.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fleet_dashboard import auth
from fleet_dashboard.dashboard_service import DashboardService, Filters, TenantScopeError, resolve_scope

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _bearer_token(headers) -> str | None:
    value = headers.get("Authorization", "")
    if not value.startswith("Bearer "):
        return None
    token = value[len("Bearer "):].strip()
    return token or None


def make_handler(service: DashboardService) -> type[BaseHTTPRequestHandler]:
    """Build a BaseHTTPRequestHandler subclass bound to `service`.

    A fresh class per server instance (rather than a module-level
    singleton) so tests can spin up independent servers, each wired to
    its own `DashboardService`/tenant data, without cross-talk.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "FleetDashboard/0.1"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            pass  # keep test/demo output quiet; nothing security-relevant is suppressed

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._send_json(200, {"status": "ok"})
                return
            if parsed.path == "/api/cards":
                self._handle_cards(parsed)
                return
            if parsed.path in ("/", "/index.html"):
                self._serve_static("index.html", "text/html; charset=utf-8")
                return
            self._send_json(404, {"error": "not found"})

        def _serve_static(self, name: str, content_type: str) -> None:
            path = _STATIC_DIR / name
            try:
                body = path.read_bytes()
            except FileNotFoundError:
                self._send_json(404, {"error": "not found"})
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle_cards(self, parsed) -> None:
            qs = parse_qs(parsed.query)
            token = _bearer_token(self.headers) or _first(qs, "token")
            authorized = auth.authorized_tenant_ids(token)

            requested_tenant_id = _first(qs, "tenant_id")
            scope_all = _first(qs, "scope") == "all_tenants"

            try:
                tenant_scope = resolve_scope(
                    authorized_tenant_ids=authorized,
                    requested_tenant_id=requested_tenant_id,
                    scope_all=scope_all,
                )
            except TenantScopeError as exc:
                self._send_json(400, {"error": str(exc)})
                return

            filters = Filters(
                repository=_first(qs, "repository"),
                team=_first(qs, "team"),
                cloud=_first(qs, "cloud"),
                column=_first(qs, "column"),
                autonomy_level=_first(qs, "autonomy_level"),
            )
            result = service.poll(tenant_scope=tenant_scope, filters=filters)
            self._send_json(200, result)

    return Handler


def _first(qs: dict, key: str) -> str | None:
    values = qs.get(key)
    return values[0] if values else None


def serve(service: DashboardService, *, host: str, port: int) -> ThreadingHTTPServer:
    """Construct (but do not block on) a running server. Callers own its
    lifecycle: `server.serve_forever()` to run, `server.shutdown()` +
    `server.server_close()` to stop -- exactly the pattern the test
    suite uses to run a real server on a background thread per test.
    """
    handler_cls = make_handler(service)
    httpd = ThreadingHTTPServer((host, port), handler_cls)
    return httpd


def run_in_background(service: DashboardService, *, host: str, port: int) -> tuple[ThreadingHTTPServer, threading.Thread]:
    httpd = serve(service, host=host, port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread
