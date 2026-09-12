"""Gate approver resolution (master spec Sec. 12.1): the default
authorized approver is the Jira issue's current Assignee at the moment
the gate fires, or its Reporter if unassigned.

Cross-deliverable contract gap flagged for human review: D4's own
`get-issue` (F3's issue-tracker contract, `jira_client.JiraClient.
get_issue`) does not surface `assignee`/`reporter` -- neither is part of
F3's schema (`services/mcp-stubs/issue-tracker/schema/
issue-tracker.schema.json`), and `JiraClient.get_issue` only ever
extracts the fixed field list it was built against (summary,
description, status, issuetype, labels, issuelinks). That is correct
for D4's own scope (gate-approver identity was not D4's concern), but it
means D9 cannot resolve an approver through the published `get_issue`
wrapper alone.

Rather than reimplementing Jira Cloud auth/HTTP handling, or hand-
editing D4's code (out of bounds for this deliverable), this module
reuses the real, already-authenticated `JiraClient` instance's own
transport (`_request`, which already carries auth headers, error
mapping, and the mock/live base_url) to ask for the two additional
fields Jira Cloud REST API v3 genuinely returns on every issue:
`fields.assignee` and `fields.reporter`. This is the same HTTP
endpoint (`GET /rest/api/3/issue/{key}`) `get_issue` already calls, just
requesting different fields -- not a parallel client, and not a
reimplementation of Jira's request signing/auth.

A human should decide whether `assignee`/`reporter` ought to become
first-class fields of F3's get-issue contract (making this reach into
`_request` unnecessary) -- flagged here rather than resolved
unilaterally by editing D4.
"""

from __future__ import annotations

from dataclasses import dataclass

from issue_tracker.errors import IssueTrackerError
from issue_tracker.jira_client import JiraClient


class ApproverResolutionError(Exception):
    pass


@dataclass(frozen=True)
class ApproverIdentity:
    """The authoritative person entitled to act on a gate, resolved at
    the moment the gate fired -- Sec. 12.1: "the Jira issue's current
    Assignee ... at the moment the gate fires. If the issue is
    unassigned, the Reporter is the default approver."""

    account_id: str
    display_name: str | None
    source: str  # "assignee" | "reporter"


def resolve_default_approver(client: JiraClient, *, issue_key: str) -> ApproverIdentity:
    """Reads the issue's current assignee/reporter for real (against
    whatever Jira -- live or, in this environment, D4's own mock --
    `client` is configured for) and applies Sec. 12.1's fallback rule.
    """
    try:
        resp = client._request(  # noqa: SLF001 -- see module docstring
            "GET",
            f"/rest/api/3/issue/{issue_key}",
            params={"fields": "assignee,reporter"},
        )
    except IssueTrackerError as exc:
        raise ApproverResolutionError(f"could not resolve approver for {issue_key!r}: {exc}") from exc

    fields = resp.json().get("fields", {})
    assignee = fields.get("assignee")
    if assignee:
        return ApproverIdentity(
            account_id=assignee["accountId"],
            display_name=assignee.get("displayName"),
            source="assignee",
        )

    reporter = fields.get("reporter")
    if reporter:
        return ApproverIdentity(
            account_id=reporter["accountId"],
            display_name=reporter.get("displayName"),
            source="reporter",
        )

    raise ApproverResolutionError(
        f"issue {issue_key!r} has neither an assignee nor a reporter -- Sec. 12.1's default-approver "
        "rule has nothing to resolve to; a configured backup approver must be used instead."
    )
