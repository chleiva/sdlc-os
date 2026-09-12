"""A thin, real, runnable stdlib `http.server` adapter around
`JobDispatcher` -- the actual webhook-receiver process (master spec Sec.
14.11: "deployed with ordinary web-service redundancy: multiple
replicas behind the control plane's load balancer").

Deliberately dependency-light, same choice `fleet-dashboard` and D4's
`webhook_relay.py` already made in this repo: `http.server` from the
standard library, no web framework. `JobDispatcher.handle_webhook` does
all real work; this module only translates HTTP <-> that call and maps
its exceptions to status codes.

Endpoints:
  * `POST /webhook` -- the job-dispatcher endpoint the Jira Automation
    rule (via D4's signed relay, see
    `services/issue-tracker/src/issue_tracker/webhook_relay.py`) posts
    to. Sec. 17.3 headers + JSON body in, one of:
      - 202 {"outcome": "run_created", ...}   -- capacity was available
      - 202 {"outcome": "queued", ...}          -- Sec. 14.11 queued, not dropped
      - 401 {"error": "..."}                    -- Sec. 17.3 auth rejection
      - 400 {"error": "..."}                    -- tenant resolution failure
      - 502 {"error": "..."}                    -- a downstream (Registry/Jira) failure
  * `GET /healthz` -- load-balancer health check.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from job_dispatcher.dispatcher import DispatchError, DispatchResult, JobDispatcher
from job_dispatcher.tenant_resolution import TenantResolutionError
from job_dispatcher.webhook_auth import AuthenticationError


def _result_to_wire(result: DispatchResult) -> dict:
    body = {
        "outcome": result.outcome,
        "tenant_id": result.tenant_id,
        "issue_key": result.jira_key,
    }
    if result.outcome == "run_created":
        run = result.run
        body["run_id"] = getattr(run, "id", None)
        body["stage"] = getattr(run, "stage", None)
        body["capacity_class"] = getattr(run, "capacity_class", None)
    else:
        body["queue_depth"] = result.queue_depth
        body["capacity_outcome"] = result.capacity_outcome.value if result.capacity_outcome else None
        body["comment_posted"] = result.comment_posted
    return body


def make_handler(dispatcher: JobDispatcher) -> type[BaseHTTPRequestHandler]:
    """Build a BaseHTTPRequestHandler subclass bound to `dispatcher`.

    A fresh class per server instance (not a module-level singleton) so
    tests can run independent servers -- each wired to its own
    JobDispatcher / tenant data -- without cross-talk, same pattern
    fleet_dashboard.http_app already uses in this repo.
    """

    class Handler(BaseHTTPRequestHandler):
        server_version = "JobDispatcher/0.1"

        def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
            pass  # tests assert on behavior, not stdout

        def _send_json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            if self.path == "/healthz":
                self._send_json(200, {"status": "ok"})
                return
            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/webhook":
                self._send_json(404, {"error": "not found"})
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b""
            headers = {k: v for k, v in self.headers.items()}

            try:
                result = dispatcher.handle_webhook(headers=headers, body=raw_body)
            except AuthenticationError as exc:
                # Sec. 17.3: rejected before tenant resolution/capacity
                # logic ever ran -- see webhook_auth.py's call-order
                # guarantee. 401, never a 200 that could be mistaken
                # for "accepted".
                self._send_json(401, {"error": str(exc), "reject_reason": exc.rejected.reject_reason})
                return
            except TenantResolutionError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except DispatchError as exc:
                self._send_json(502, {"error": str(exc)})
                return

            self._send_json(202, _result_to_wire(result))

    return Handler


def serve(dispatcher: JobDispatcher, *, host: str, port: int) -> ThreadingHTTPServer:
    """Construct (but do not block on) a running server. Caller owns its
    lifecycle: `server.serve_forever()` to run, `server.shutdown()` +
    `server.server_close()` to stop.
    """
    handler_cls = make_handler(dispatcher)
    return ThreadingHTTPServer((host, port), handler_cls)


def run_in_background(dispatcher: JobDispatcher, *, host: str, port: int) -> tuple[ThreadingHTTPServer, threading.Thread]:
    httpd = serve(dispatcher, host=host, port=port)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread
