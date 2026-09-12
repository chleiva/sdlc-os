"""A local mock HTTP server mimicking Jira Cloud REST API v3's actual
response shapes (create issue, get issue, transitions, comments, issue
links, the `/search/jql` endpoint) closely enough for `jira_client.py`
to be exercised end to end without a live Jira org.

This is deliberately *not* a re-implementation of Jira's business
logic in general -- it is an in-memory issue store with just the
endpoints and status codes `jira_client.py` calls, wired up with
`http.server.ThreadingHTTPServer` (stdlib only, no extra dependency
needed to stand up a realistic mock). See services/issue-tracker/
SETUP.md for exactly what is real (the request/response shapes,
matched against the Cloud REST API v3 reference) versus what is
necessarily mocked here (no live Atlassian data center on the other
end).
"""

from __future__ import annotations

import itertools
import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_ISSUE_KEY_RE = re.compile(r"^/rest/api/3/issue/([A-Z][A-Z0-9]*-[0-9]+)(/.*)?$")

# A small, fixed workflow every mock issue starts in, with a handful of
# realistic transitions -- deliberately including some *not* in any
# tenant's configured gate-status allowlist, so tests can prove
# `transition_status` refuses to even ask the mock for one of those.
_TRANSITIONS = [
    {"id": "11", "name": "Start Progress", "to": {"name": "In Progress"}},
    {"id": "21", "name": "Move to Review", "to": {"name": "In Review"}},
    {"id": "31", "name": "Done", "to": {"name": "Done"}},
    {"id": "41", "name": "Block", "to": {"name": "Blocked"}},
]


@dataclass
class MockIssue:
    key: str
    project_key: str
    issue_type: str
    summary: str
    description: dict[str, Any]
    status: str = "Selected for Development"
    labels: list[str] = field(default_factory=list)
    parent_key: str | None = None
    fields_extra: dict[str, Any] = field(default_factory=dict)
    issuelinks: list[dict[str, Any]] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    archived: bool = False

    def to_wire(self) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "summary": self.summary,
            "description": self.description,
            "status": {"name": self.status},
            "issuetype": {"name": self.issue_type},
            "labels": self.labels,
            "issuelinks": self.issuelinks,
        }
        if self.archived:
            fields["_archived"] = True
        fields.update(self.fields_extra)
        return {"id": self.key.split("-")[1], "key": self.key, "self": f"/rest/api/3/issue/{self.key}",
                "fields": fields}


class JiraMockStore:
    """In-memory issue store + a couple of injectable failure hooks,
    shared across the mock server's request handlers (one store per
    server instance, so tests get a clean slate per fixture)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._issues: dict[str, MockIssue] = {}
        self._counter = itertools.count(100)
        self._comment_counter = itertools.count(1000)
        # tenant_id -> forced HTTP status for the *next* call only (rate
        # limiting / upstream-unavailable / permission simulation).
        self.force_status: dict[str, int] = {}
        self.force_retry_after: int | None = None

    def seed(self, issue: MockIssue) -> MockIssue:
        with self._lock:
            self._issues[issue.key] = issue
        return issue

    def new_key(self, project_key: str) -> str:
        return f"{project_key}-{next(self._counter)}"

    def get(self, key: str) -> MockIssue | None:
        return self._issues.get(key)

    def all(self) -> list[MockIssue]:
        return list(self._issues.values())

    def next_comment_id(self) -> str:
        return str(next(self._comment_counter))


def _read_json(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length).decode("utf-8"))


def make_handler(store: JiraMockStore):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            pass

        def _send(self, status: int, body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> None:
            payload = b"" if body is None else json.dumps(body).encode("utf-8")
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if payload:
                self.wfile.write(payload)

        def _maybe_force(self) -> bool:
            """Apply a one-shot forced-failure hook if the test set one
            via `store.force_status`, keyed by the `X-Force-Status` test
            header the client-under-test can optionally set. Returns
            True if a forced response was sent (caller should stop)."""
            forced = self.headers.get("X-Force-Status")
            if forced:
                status = int(forced)
                headers = {}
                if status == 429:
                    headers["Retry-After"] = str(store.force_retry_after or 5)
                self._send(status, {"errorMessages": [f"forced {status} for test"]}, headers)
                return True
            return False

        # -- POST /rest/api/3/issue ---------------------------------------------------

        def do_POST(self):  # noqa: N802
            if self._maybe_force():
                return
            path = self.path.split("?")[0]

            if path == "/rest/api/3/issue":
                self._create_issue()
                return
            if path == "/rest/api/3/issueLink":
                self._create_issue_link()
                return
            if path == "/rest/api/3/search/jql":
                self._search_jql()
                return
            m = _ISSUE_KEY_RE.match(path)
            if m and m.group(2) == "/transitions":
                self._do_transition(m.group(1))
                return
            if m and m.group(2) == "/comment":
                self._add_comment(m.group(1))
                return
            self._send(404, {"errorMessages": ["No such resource."]})

        def do_GET(self):  # noqa: N802
            if self._maybe_force():
                return
            path = self.path.split("?")[0]
            m = _ISSUE_KEY_RE.match(path)
            if m and m.group(2) in (None, ""):
                self._get_issue(m.group(1))
                return
            if m and m.group(2) == "/transitions":
                self._get_transitions(m.group(1))
                return
            self._send(404, {"errorMessages": ["No such resource."]})

        # -- handlers ---------------------------------------------------

        def _create_issue(self) -> None:
            body = _read_json(self)
            fields = body.get("fields", {})
            project_key = fields["project"]["key"]
            issue_type = fields["issuetype"]["name"]
            key = store.new_key(project_key)
            parent_key = fields.get("parent", {}).get("key") if isinstance(fields.get("parent"), dict) else None
            extra = {k: v for k, v in fields.items()
                     if k not in ("project", "issuetype", "summary", "description", "labels", "parent")}
            issue = MockIssue(
                key=key,
                project_key=project_key,
                issue_type=issue_type,
                summary=fields["summary"],
                description=fields.get("description", {}),
                labels=list(fields.get("labels", [])),
                parent_key=parent_key,
                fields_extra=extra,
            )
            store.seed(issue)
            self._send(201, {"id": key.split("-")[1], "key": key, "self": f"/rest/api/3/issue/{key}"})

        def _create_issue_link(self) -> None:
            body = _read_json(self)
            outward_key = body["outwardIssue"]["key"]
            inward_key = body["inwardIssue"]["key"]
            link_type = body["type"]["name"]
            outward_issue = store.get(outward_key)
            inward_issue = store.get(inward_key)
            if outward_issue is None or inward_issue is None:
                self._send(404, {"errorMessages": ["One or both linked issues do not exist."]})
                return
            outward_issue.issuelinks.append({"type": {"name": link_type}, "inwardIssue": {"key": inward_key}})
            inward_issue.issuelinks.append({"type": {"name": link_type}, "outwardIssue": {"key": outward_key}})
            self._send(201, {})

        def _search_jql(self) -> None:
            body = _read_json(self)
            jql = body.get("jql", "")
            # Minimal JQL-shaped matcher: this mock only ever needs to
            # answer the exact `project = .. AND issuetype = .. AND
            # summary = ..  [AND parent = ..]` query jira_client.py
            # builds for its idempotency check -- not a JQL parser.
            def extract(field: str) -> str | None:
                pat = re.compile(rf'{field}\s*=\s*"((?:[^"\\]|\\.)*)"')
                mo = pat.search(jql)
                if not mo:
                    return None
                return mo.group(1).replace('\\"', '"').replace("\\\\", "\\")

            project_key = extract("project")
            issue_type = extract("issuetype")
            summary = extract("summary")
            parent_key = extract("parent")

            matches = []
            for issue in store.all():
                if project_key and issue.project_key != project_key:
                    continue
                if issue_type and issue.issue_type != issue_type:
                    continue
                if summary and issue.summary != summary:
                    continue
                if parent_key and issue.parent_key != parent_key:
                    continue
                matches.append({"key": issue.key, "fields": {"summary": issue.summary}})
            self._send(200, {"issues": matches, "nextPageToken": None})

        def _get_issue(self, key: str) -> None:
            issue = store.get(key)
            if issue is None:
                self._send(404, {"errorMessages": [f"Issue does not exist: {key}"]})
                return
            self._send(200, issue.to_wire())

        def _get_transitions(self, key: str) -> None:
            issue = store.get(key)
            if issue is None:
                self._send(404, {"errorMessages": [f"Issue does not exist: {key}"]})
                return
            self._send(200, {"transitions": _TRANSITIONS})

        def _do_transition(self, key: str) -> None:
            issue = store.get(key)
            if issue is None:
                self._send(404, {"errorMessages": [f"Issue does not exist: {key}"]})
                return
            body = _read_json(self)
            transition_id = body["transition"]["id"]
            match = next((t for t in _TRANSITIONS if t["id"] == transition_id), None)
            if match is None:
                self._send(404, {"errorMessages": [f"No such transition id: {transition_id}"]})
                return
            issue.status = match["to"]["name"]
            self._send(204)

        def _add_comment(self, key: str) -> None:
            issue = store.get(key)
            if issue is None:
                self._send(404, {"errorMessages": [f"Issue does not exist: {key}"]})
                return
            body = _read_json(self)
            comment_id = store.next_comment_id()
            comment = {"id": comment_id, "body": body.get("body"), "created": "2026-09-12T10:00:00.000+0000"}
            issue.comments.append(comment)
            self._send(201, comment)

    return Handler


def start_mock_server(host: str = "127.0.0.1", port: int = 0) -> tuple[ThreadingHTTPServer, JiraMockStore]:
    store = JiraMockStore()
    server = ThreadingHTTPServer((host, port), make_handler(store))
    return server, store
