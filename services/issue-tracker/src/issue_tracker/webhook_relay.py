"""The Automation-rule payload contract, and the component that turns
a Jira Automation rule firing into a signed dispatch to D1's job
dispatcher.

D1 (the job dispatcher) does not exist yet (Wave 1, built in parallel).
Per the brief: "your webhook-receiving side (or the Automation-rule
payload contract you define, since D1 doesn't exist yet to receive it)
must make it structurally impossible for a story lacking the opt-in
label/type to produce a dispatch." This module is that contract: a
`RelayApp.handle_automation_event` call either raises `NotOptedInError`
(no dispatch produced -- see `gating.py`) or returns a `SignedDispatch`
carrying a real Sec. 17.3 HMAC-signed payload. There is no third path
that produces a dispatch without going through the opt-in check.

Why this exists as its own hop instead of Jira Automation calling D1
directly: Jira Cloud's native "Automation -> Send web request" action
has no built-in way to *compute* a per-tenant HMAC signature at
send-time (it can attach static header values, but Sec. 17.3's scheme
needs a signature computed fresh over this specific body + timestamp,
which is a cryptographic operation the Automation UI does not expose).
This relay is the thing that receives the Automation rule's plain
(TLS-protected, but unsigned) request and produces the real signed
request D1 verifies -- see SETUP.md's "spec ambiguity" note, which
flags this as something a human should confirm against their org's
actual Jira plan (a small minority of Automation configurations may
support a native signed-webhook feature that would make this hop
unnecessary; absent confirmation of that, this relay is the safe
default).

This module intentionally does not depend on any particular HTTP
server framework -- `handle_automation_event` is a plain function
so it is trivially unit-testable; `serve_forever`/the `http.server`
wiring at the bottom is a thin, real, runnable adapter around it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from issue_tracker import gating, webhook_signing
from issue_tracker.config import TenantJiraConfig
from issue_tracker.errors import IssueTrackerError
from issue_tracker.gating import NotOptedInError, OptedInStory
from issue_tracker.jira_client import JiraClient


class RelayError(Exception):
    """A malformed Automation-rule payload or an unresolvable
    repository -- distinct from `NotOptedInError`, which is a *correct*
    "do not dispatch" decision, not an error."""


@dataclass(frozen=True)
class AutomationEventPayload:
    """What the Jira Automation rule's "send web request" action body
    carries (see automation-rule.json's action.value.body). Exactly
    enough to re-fetch full issue detail and resolve the target
    repository per Sec. 4.4 -- nothing else is trusted from this
    payload; opt-in status and current issue state are always
    independently re-fetched via `get-issue`, never taken from this
    payload as-is (Jira Automation's own trigger already ran the
    label/status condition, but this relay never trusts that alone --
    see `gating.py`).
    """

    tenant_id: str
    issue_key: str
    project_key: str

    @staticmethod
    def from_json(raw: dict) -> "AutomationEventPayload":
        missing = [f for f in ("tenant_id", "issue_key", "project_key") if not raw.get(f)]
        if missing:
            raise RelayError(f"Automation event payload missing required field(s): {missing}")
        return AutomationEventPayload(
            tenant_id=raw["tenant_id"], issue_key=raw["issue_key"], project_key=raw["project_key"]
        )


@dataclass(frozen=True)
class SignedDispatch:
    """A dispatch request ready to send to D1's job-dispatcher
    endpoint: the exact bytes that were signed, plus the Sec. 17.3
    headers. Constructing one of these *requires* an `OptedInStory` --
    there is no code path in this module that builds a `SignedDispatch`
    without first obtaining one from `gating.require`.
    """

    url: str
    headers: dict[str, str]
    body: bytes
    opted_in_story: OptedInStory

    def body_json(self) -> dict:
        return json.loads(self.body)


@dataclass
class RelayApp:
    jira_client_for_tenant: Callable[[str], JiraClient]
    config_for_tenant: Callable[[str], TenantJiraConfig]
    secret_for_tenant: Callable[[str], bytes | None]
    repository_for_project: Callable[[str, str], str | None]
    dispatcher_url: str

    def handle_automation_event(self, raw_event: dict) -> SignedDispatch:
        """The single entry point a Jira Automation rule's web request
        (or a test standing in for one) calls into. Returns a
        `SignedDispatch` on success. Raises `NotOptedInError` if the
        issue is not opted in (a legitimate, expected outcome -- callers
        must treat this as "no dispatch", not as a failure to log and
        retry) or `RelayError`/`IssueTrackerError` for a malformed
        payload or an unreachable Jira.
        """
        payload = AutomationEventPayload.from_json(raw_event)

        config = self.config_for_tenant(payload.tenant_id)
        client = self.jira_client_for_tenant(payload.tenant_id)

        issue_result = client.get_issue(issue_key=payload.issue_key)
        if issue_result["outcome"] == "error":
            err = issue_result["error"]
            raise IssueTrackerErrorFromWire(err["code"], err["message"])
        if issue_result["outcome"] == "empty":
            raise RelayError(f"Issue {payload.issue_key!r} could not be refetched: {issue_result['reason']}")

        issue = issue_result["data"]

        # The one and only gate. `gating.require` either returns an
        # OptedInStory (dispatch may proceed) or raises NotOptedInError
        # (it must not proceed) -- there is no third outcome, and this
        # is the only place in this class that is allowed to construct
        # a SignedDispatch below.
        opted_in = gating.require(
            tenant_id=payload.tenant_id,
            issue_key=issue["issue_key"],
            status=issue["status"],
            labels=issue.get("labels", []),
            issue_type=issue["issue_type"],
            config=config,
        )

        repository = self.repository_for_project(payload.tenant_id, payload.project_key)
        if repository is None:
            raise RelayError(
                f"No repository is configured for tenant {payload.tenant_id!r} project "
                f"{payload.project_key!r}; refusing to dispatch with an unresolved target repository."
            )

        secret = self.secret_for_tenant(payload.tenant_id)
        if secret is None:
            raise RelayError(f"No per-tenant webhook signing key configured for tenant {payload.tenant_id!r}.")

        body_obj = {
            "tenant_id": payload.tenant_id,
            "issue_key": issue["issue_key"],
            "project_key": payload.project_key,
            "repository": repository,
            "status": issue["status"],
            "labels": issue.get("labels", []),
        }
        body = json.dumps(body_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
        headers = webhook_signing.sign_request(tenant_id=payload.tenant_id, secret=secret, body=body)
        headers["Content-Type"] = "application/json"

        return SignedDispatch(url=self.dispatcher_url, headers=headers, body=body, opted_in_story=opted_in)


class IssueTrackerErrorFromWire(Exception):
    """Re-raised at the relay boundary when `get-issue` itself returned
    an ErrorResult (e.g. upstream-unavailable) -- kept distinct from
    `RelayError` so callers can tell "Jira call failed" from "payload
    malformed" from "not opted in"."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# -- a thin, real, runnable HTTP adapter around RelayApp ---------------------------------------------------


def make_handler(app: RelayApp, forward: Callable[[SignedDispatch], None] | None = None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - stdlib signature
            pass  # quiet; tests assert on behavior, not stdout

        def do_POST(self):  # noqa: N802 - stdlib method name
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            try:
                event = json.loads(raw.decode("utf-8"))
                dispatch = app.handle_automation_event(event)
            except NotOptedInError as exc:
                self.send_response(200)  # not an error: a correct no-op
                self._write_json({"dispatched": False, "reason": str(exc)})
                return
            except (RelayError, IssueTrackerErrorFromWire, IssueTrackerError) as exc:
                self.send_response(502)
                self._write_json({"dispatched": False, "error": str(exc)})
                return

            if forward is not None:
                forward(dispatch)
            self.send_response(202)
            self._write_json({"dispatched": True, "issue_key": dispatch.opted_in_story.issue_key})

        def _write_json(self, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve_forever(app: RelayApp, host: str = "127.0.0.1", port: int = 0,
                   forward: Callable[[SignedDispatch], None] | None = None) -> ThreadingHTTPServer:
    """Starts a real (if minimal) HTTP server wrapping `RelayApp` and
    returns it already listening (caller is responsible for calling
    `.shutdown()`/`.server_close()`, typically from a background
    thread in tests -- see tests/test_webhook_relay.py)."""
    server = ThreadingHTTPServer((host, port), make_handler(app, forward))
    return server
