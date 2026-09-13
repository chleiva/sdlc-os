"""Real Jira Cloud REST API v3 HTTP client.

Implements F3's issue-tracker contract (create-epic, create-story,
get-issue, transition-status, post-comment) against the actual Jira
Cloud REST API v3 request/response shapes -- ADF bodies, issue links,
the transitions sub-resource, the new `/search/jql` endpoint. Every
public method here returns a plain dict already shaped like one branch
of the F3 schema's oneOf (ok/empty/error) -- see `result.py`.

This module makes real HTTP calls via `requests`. In this environment
there is no live Jira org to call, so tests point `base_url` at
`mocks/jira_mock_server.py` instead (see tests/conftest.py and
SETUP.md). Nothing about the request-building or response-parsing code
below is aware that it is talking to a mock -- the mock is the only
thing standing in for a real Atlassian data center.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from typing import Any

import requests

from issue_tracker import adf
from issue_tracker.config import TenantJiraConfig
from issue_tracker.errors import IssueTrackerError, NotFoundError, from_http_response
from issue_tracker.result import Result

_ISSUE_KEY_FIELDS = ["summary", "description", "status", "issuetype", "labels", "issuelinks"]

_LINK_TYPE_NAME = "Blocks"  # Jira's stock issue-link type; outward "blocks" / inward "is blocked by"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _escape_jql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


@dataclass
class JiraClient:
    config: TenantJiraConfig
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()

    # -- transport ---------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.config.auth_mode == "oauth_bearer":
            if not self.config.oauth_bearer_token:
                raise ValueError("oauth_bearer auth_mode requires oauth_bearer_token")
            headers["Authorization"] = f"Bearer {self.config.oauth_bearer_token}"
        elif self.config.auth_mode == "basic":
            if not (self.config.basic_auth_email and self.config.basic_auth_api_token):
                raise ValueError("basic auth_mode requires basic_auth_email and basic_auth_api_token")
        else:
            raise ValueError(f"unknown auth_mode {self.config.auth_mode!r}")
        return headers

    def _auth(self) -> tuple[str, str] | None:
        if self.config.auth_mode == "basic":
            return (self.config.basic_auth_email, self.config.basic_auth_api_token)
        return None

    def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None,
                 params: dict[str, Any] | None = None) -> requests.Response:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        try:
            resp = self.session.request(
                method,
                url,
                headers=self._headers(),
                auth=self._auth(),
                json=json_body,
                params=params,
                timeout=self.config.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            from issue_tracker.errors import UpstreamUnavailableError

            raise UpstreamUnavailableError(f"Could not reach Jira at {url}: {exc}") from exc
        if resp.status_code >= 400:
            raise from_http_response(resp.status_code, resp.text, resp.headers)
        return resp

    # -- create-epic ---------------------------------------------------

    def create_epic(self, *, project_key: str, summary: str, description: str,
                     acceptance_criteria: list[str], size: str | None = None,
                     labels: list[str] | None = None) -> dict[str, Any]:
        try:
            existing = self._find_existing(project_key=project_key, issue_type="Epic", summary=summary)
            if existing is not None:
                return Result.empty(
                    f"An epic with summary '{summary}' already exists in project {project_key}; no duplicate created.",
                    existing_issue_key=existing,
                ).to_wire()

            full_description = description
            if acceptance_criteria:
                bullets = "\n".join(f"- {ac}" for ac in acceptance_criteria)
                full_description = f"{description}\n\nAcceptance criteria:\n{bullets}"

            fields: dict[str, Any] = {
                "project": {"key": project_key},
                "issuetype": {"name": "Epic"},
                "summary": summary,
                "description": adf.text_to_adf(full_description),
            }
            all_labels = list(labels or [])
            if size:
                if self.config.size_field_mode == "label":
                    all_labels.append(self.config.size_label(size))
                else:
                    if not self.config.size_custom_field_id:
                        raise ValueError("size_field_mode=customfield requires size_custom_field_id")
                    fields[self.config.size_custom_field_id] = size
            if all_labels:
                fields["labels"] = all_labels

            resp = self._request("POST", "/rest/api/3/issue", json_body={"fields": fields})
            body = resp.json()
            issue_key = body["key"]
            url = f"{self.config.base_url.rstrip('/')}/browse/{issue_key}"
            return Result.ok({"issue_key": issue_key, "url": url}).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    # -- create-story ---------------------------------------------------

    def create_story(self, *, project_key: str, epic_key: str, summary: str, description: str,
                      size: str, depends_on: list[str] | None = None,
                      labels: list[str] | None = None) -> dict[str, Any]:
        try:
            existing = self._find_existing(project_key=project_key, issue_type="Story", summary=summary,
                                            epic_key=epic_key)
            if existing is not None:
                return Result.empty(
                    f"A story with summary '{summary}' already exists under {epic_key}; no duplicate created.",
                    existing_issue_key=existing,
                ).to_wire()

            fields: dict[str, Any] = {
                "project": {"key": project_key},
                "issuetype": {"name": "Story"},
                "summary": summary,
                "description": adf.text_to_adf(description),
            }
            if self.config.epic_link_mode == "parent":
                fields["parent"] = {"key": epic_key}
            else:
                if not self.config.epic_link_custom_field_id:
                    raise ValueError("epic_link_mode=customfield requires epic_link_custom_field_id")
                fields[self.config.epic_link_custom_field_id] = epic_key

            all_labels = list(labels or [])
            if self.config.size_field_mode == "label":
                all_labels.append(self.config.size_label(size))
            else:
                if not self.config.size_custom_field_id:
                    raise ValueError("size_field_mode=customfield requires size_custom_field_id")
                fields[self.config.size_custom_field_id] = size
            if all_labels:
                fields["labels"] = all_labels

            resp = self._request("POST", "/rest/api/3/issue", json_body={"fields": fields})
            body = resp.json()
            issue_key = body["key"]

            # Dependency sequencing as *native Jira issue links*
            # (blocks / is-blocked-by), never a private ordering only
            # this System knows -- master spec Sec. 4.4.
            for dep_key in depends_on or []:
                self._create_blocks_link(blocker_key=dep_key, blocked_key=issue_key)

            url = f"{self.config.base_url.rstrip('/')}/browse/{issue_key}"
            return Result.ok({"issue_key": issue_key, "url": url, "epic_key": epic_key}).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    def _create_blocks_link(self, *, blocker_key: str, blocked_key: str) -> None:
        """`blocker_key` blocks `blocked_key` (i.e. `blocked_key` is
        blocked by `blocker_key`), expressed as a native Jira issue
        link so it is inspectable in Jira's own UI, not a side table
        only this System reads."""
        self._request(
            "POST",
            "/rest/api/3/issueLink",
            json_body={
                "type": {"name": _LINK_TYPE_NAME},
                "outwardIssue": {"key": blocker_key},
                "inwardIssue": {"key": blocked_key},
            },
        )

    # -- get-issue ---------------------------------------------------

    def get_issue(self, *, issue_key: str) -> dict[str, Any]:
        try:
            resp = self._request(
                "GET",
                f"/rest/api/3/issue/{issue_key}",
                params={"fields": ",".join(_ISSUE_KEY_FIELDS)},
            )
            body = resp.json()
            fields = body["fields"]

            if fields.get("_archived"):
                # The mock's convention for an issue that resolves but
                # whose content is no longer retrievable -- distinct
                # from not-found (key never existed) and
                # permission-denied (access explicitly refused).
                return Result.empty(
                    f"Issue '{issue_key}' resolves, but has been archived; its content is no longer retrievable."
                ).to_wire()

            labels: list[str] = list(fields.get("labels", []))
            size = None
            if self.config.size_field_mode == "label":
                for label in labels:
                    if label.startswith("size:"):
                        size = label.split(":", 1)[1]
                        break
            elif self.config.size_custom_field_id:
                size = fields.get(self.config.size_custom_field_id)

            links: list[dict[str, str]] = []
            for link in fields.get("issuelinks", []):
                type_name = link.get("type", {}).get("name")
                if type_name != _LINK_TYPE_NAME:
                    continue
                if "outwardIssue" in link:
                    # this issue is the inward side of "Blocks" -> it is blocked-by
                    links.append({"type": "is-blocked-by", "issue_key": link["outwardIssue"]["key"]})
                elif "inwardIssue" in link:
                    # this issue is the outward side -> it blocks the other
                    links.append({"type": "blocks", "issue_key": link["inwardIssue"]["key"]})

            data = {
                "issue_key": body["key"],
                "summary": fields.get("summary", ""),
                "description": adf.adf_to_text(fields.get("description")),
                "status": fields.get("status", {}).get("name", ""),
                "issue_type": fields.get("issuetype", {}).get("name", ""),
            }
            if size:
                data["size"] = size
            if labels:
                data["labels"] = labels
            if links:
                data["links"] = links
            return Result.ok(data).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    # -- transition-status ---------------------------------------------------

    def transition_status(self, *, issue_key: str, target_status: str, comment: str | None = None) -> dict[str, Any]:
        try:
            if target_status not in self.config.allowed_target_statuses():
                # Sec. 4.4: "Jira status stays coarse, deliberately."
                # This is enforced here as a hard, in-code allowlist --
                # not merely a documented convention callers might
                # forget. A caller asking for an internal pipeline
                # stage (e.g. "research", "implementation",
                # "verification") is a programming error, surfaced as
                # permission-denied (this credential/config is not
                # authorized to request that transition) rather than
                # silently performing it or silently ignoring it.
                from issue_tracker.errors import PermissionDeniedError

                raise PermissionDeniedError(
                    f"Refusing to request transition to {target_status!r}: not one of the configured "
                    f"gate statuses {sorted(self.config.allowed_target_statuses())}. Jira's status column "
                    "stays coarse by design (master spec Sec. 4.4) -- fine-grained pipeline stages belong "
                    "in the Run Registry / Fleet Control Dashboard (D8), never in Jira's status column."
                )

            current = self._request("GET", f"/rest/api/3/issue/{issue_key}",
                                     params={"fields": "status"}).json()
            previous_status = current["fields"]["status"]["name"]
            if previous_status == target_status:
                return Result.empty(
                    f"Issue '{issue_key}' is already in status '{target_status}'; no transition performed."
                ).to_wire()

            transitions = self._request("GET", f"/rest/api/3/issue/{issue_key}/transitions").json()["transitions"]
            match = next((t for t in transitions if t["to"]["name"] == target_status), None)
            if match is None:
                raise NotFoundError(
                    f"No transition to status '{target_status}' is available for issue '{issue_key}' "
                    f"from its current status '{previous_status}'."
                )

            self._request(
                "POST",
                f"/rest/api/3/issue/{issue_key}/transitions",
                json_body={"transition": {"id": match["id"]}},
            )
            if comment:
                self._request(
                    "POST",
                    f"/rest/api/3/issue/{issue_key}/comment",
                    json_body={"body": adf.text_to_adf(comment)},
                )

            return Result.ok({
                "issue_key": issue_key,
                "previous_status": previous_status,
                "new_status": target_status,
                "transitioned_at": _now_iso(),
            }).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    # -- post-comment ---------------------------------------------------

    def post_comment(self, *, issue_key: str, body: str, comment_type: str = "general") -> dict[str, Any]:
        """`comment_type` is accepted for callers' own bookkeeping (and
        was previously round-tripped to Jira as a comment `properties`
        entry) but is no longer sent to Jira at all -- a real live-run
        bug found and fixed: Jira Cloud's `POST .../comment` genuinely
        rejects a `properties` array on this endpoint (HTTP 400,
        "The JSON data provided for the property is not a valid JSON",
        reproduced against a real Jira Cloud site regardless of the
        value's shape). Real per-comment properties, if ever needed for
        real, are a *separate* real endpoint
        (`PUT /rest/api/3/comment/{commentId}/properties/{propertyKey}`,
        one call per property, after creation) -- not implemented here
        since nothing in this codebase ever reads `comment_type` back;
        it was write-only metadata that had never actually been
        exercised against a real Jira account until this bug surfaced."""
        try:
            if adf.is_empty_adf_or_text(body):
                return Result.empty("Comment body was empty after template rendering; nothing posted.").to_wire()

            resp = self._request(
                "POST",
                f"/rest/api/3/issue/{issue_key}/comment",
                json_body={"body": adf.text_to_adf(body)},
            )
            posted = resp.json()
            return Result.ok({
                "comment_id": str(posted["id"]),
                "issue_key": issue_key,
                "created_at": posted.get("created", _now_iso()),
            }).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    # -- whoami / assign-issue / list-comments (New: the async human-in-the-
    # loop path for an unattended/automatic trigger -- see
    # deploy/run-worker/jira_poll_run.py's module docstring. A run that
    # pauses for a human decision cannot block on a terminal prompt when
    # nothing started it interactively; it instead posts a real comment,
    # assigns the issue to a real person, and a later poll looks for their
    # reply comment instead.) ------------------------------------------

    def whoami(self) -> dict[str, Any]:
        """GET /rest/api/3/myself -- the real Atlassian account this
        client's own credentials belong to. Used to (a) know who to
        assign a paused run's issue to (this tool's own single-user
        assumption: whoever the configured token belongs to), and (b)
        recognize the bot's own comments so a later scan for a human's
        reply never mistakes its own notification for a decision."""
        try:
            resp = self._request("GET", "/rest/api/3/myself")
            body = resp.json()
            return Result.ok({
                "account_id": body["accountId"],
                "display_name": body.get("displayName", ""),
                "email_address": body.get("emailAddress", ""),
            }).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    def assign_issue(self, *, issue_key: str, account_id: str) -> dict[str, Any]:
        """PUT /rest/api/3/issue/{key}/assignee -- real Jira Cloud
        assignment by accountId (the only supported identifier post-GDPR;
        see Atlassian's own migration off username-based assignment)."""
        try:
            self._request(
                "PUT",
                f"/rest/api/3/issue/{issue_key}/assignee",
                json_body={"accountId": account_id},
            )
            return Result.ok({"issue_key": issue_key, "account_id": account_id}).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    def list_comments(self, *, issue_key: str) -> dict[str, Any]:
        """GET /rest/api/3/issue/{key}/comment -- every comment on the
        issue, oldest first (matches Jira's own default ordering),
        ADF bodies converted to plain text (`adf.adf_to_text`, the same
        helper `get_issue` already uses for descriptions) so a caller
        can match a human's reply against a plain-word vocabulary
        without doing its own ADF parsing."""
        try:
            resp = self._request("GET", f"/rest/api/3/issue/{issue_key}/comment")
            body = resp.json()
            comments = [
                {
                    "comment_id": str(c["id"]),
                    "author_account_id": c.get("author", {}).get("accountId", ""),
                    "body": adf.adf_to_text(c.get("body")),
                    "created_at": c.get("created", ""),
                }
                for c in body.get("comments", [])
            ]
            return Result.ok({"comments": comments}).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    # -- find-stories-in-status (New: the polling-based trigger path) ------

    def find_stories_in_status(self, *, project_key: str, status: str) -> dict[str, Any]:
        """Real JQL search (`/rest/api/3/search/jql`) for every issue key
        in `project_key` currently sitting in `status` -- the polling
        alternative to Jira Automation's push webhook (see SETUP.md's
        "poll-based bridge" option: no Automation rule, no publicly-
        reachable relay, no job-dispatcher wiring needed to prove the
        trigger path end-to-end).

        Deliberately returns only issue keys, not a dispatch decision:
        matches Sec. 4.4's "never treat being in the trigger status
        alone as sufficient" rule (`gating.py`'s docstring) -- the
        caller is expected to call `get_issue` for full detail (labels,
        issue type, description) and `gating.evaluate`/`require` on each
        candidate before treating any of them as opted in."""
        try:
            jql = (
                f'project = "{_escape_jql_string(project_key)}" '
                f'AND status = "{_escape_jql_string(status)}"'
            )
            resp = self._request(
                "POST",
                "/rest/api/3/search/jql",
                json_body={"jql": jql, "fields": ["summary"], "maxResults": 50},
            )
            keys = [issue["key"] for issue in resp.json().get("issues", [])]
            return Result.ok({"issue_keys": keys}).to_wire()
        except IssueTrackerError as exc:
            return self._error_result(exc)

    # -- helpers ---------------------------------------------------

    def _find_existing(self, *, project_key: str, issue_type: str, summary: str,
                        epic_key: str | None = None) -> str | None:
        """Idempotency check via Jira's enhanced JQL search endpoint
        (`/rest/api/3/search/jql`, the successor to the deprecated
        `/rest/api/3/search`). Returns the existing issue key, or None.
        """
        clauses = [
            f'project = "{_escape_jql_string(project_key)}"',
            f'issuetype = "{issue_type}"',
            f'summary = "{_escape_jql_string(summary)}"',
        ]
        if epic_key:
            clauses.append(f'parent = "{_escape_jql_string(epic_key)}"')
        jql = " AND ".join(clauses)
        resp = self._request(
            "POST",
            "/rest/api/3/search/jql",
            json_body={"jql": jql, "fields": ["summary"], "maxResults": 1},
        )
        issues = resp.json().get("issues", [])
        return issues[0]["key"] if issues else None

    def _error_result(self, exc: IssueTrackerError) -> dict[str, Any]:
        return Result.fail(
            exc.code, exc.message, retry_after_seconds=exc.retry_after_seconds, details=exc.details
        ).to_wire()
