"""Named error conditions for the issue-tracker MCP contract (F3).

Per master spec Sec. 7.6 / the issue-tracker schema, every tool exposes
exactly four named error conditions, distinct from a successful empty
result: not-found, permission-denied, rate-limited, upstream-unavailable.
This module is the single place that maps a Jira Cloud REST API v3 HTTP
response (or a network failure reaching it) onto one of those four --
no other error shape is allowed to leak out of `jira_client.py`.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    NOT_FOUND = "not-found"
    PERMISSION_DENIED = "permission-denied"
    RATE_LIMITED = "rate-limited"
    UPSTREAM_UNAVAILABLE = "upstream-unavailable"


RETRYABLE: dict[ErrorCode, bool] = {
    ErrorCode.NOT_FOUND: False,
    ErrorCode.PERMISSION_DENIED: False,
    ErrorCode.RATE_LIMITED: True,
    ErrorCode.UPSTREAM_UNAVAILABLE: True,
}


class IssueTrackerError(Exception):
    """Base class for every error `jira_client.JiraClient` can raise.

    Carries a named `code` (one of `ErrorCode`) plus a human-readable,
    caller-safe `message`. Callers at the MCP-contract boundary
    (`mcp_server.py`) catch this -- never a raw `requests` exception --
    and turn it into the `ErrorResult` envelope branch.
    """

    code: ErrorCode

    def __init__(self, message: str, *, retry_after_seconds: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}


class NotFoundError(IssueTrackerError):
    code = ErrorCode.NOT_FOUND


class PermissionDeniedError(IssueTrackerError):
    code = ErrorCode.PERMISSION_DENIED


class RateLimitedError(IssueTrackerError):
    code = ErrorCode.RATE_LIMITED


class UpstreamUnavailableError(IssueTrackerError):
    code = ErrorCode.UPSTREAM_UNAVAILABLE


def from_http_response(status_code: int, body_text: str, headers: dict[str, str]) -> IssueTrackerError:
    """Map a Jira Cloud REST API v3 HTTP error response to a named error.

    Jira's own status-code semantics (documented in the Cloud platform
    REST API v3 reference):
      404 -- issue/project/transition not found, or (deliberately, per
             Jira's own docs) the caller lacks even browse permission --
             Jira collapses "doesn't exist" and "you can't see it" into
             404 to avoid leaking existence. We surface that as
             not-found, which is the safe direction (never claims
             permission-denied when the resource may simply not exist).
      401/403 -- authentication/authorization failure with a resource
             Jira *does* acknowledge exists (e.g. a transition your
             account can see but not execute) -- permission-denied.
      429 -- rate-limited; Jira sends a `Retry-After` header (seconds).
      5xx / network failure -- upstream-unavailable.
    """
    if status_code == 404:
        return NotFoundError(f"Jira returned 404: {body_text[:500]}")
    if status_code in (401, 403):
        return PermissionDeniedError(f"Jira returned {status_code}: {body_text[:500]}")
    if status_code == 429:
        retry_after = headers.get("Retry-After")
        try:
            retry_after_seconds = int(retry_after) if retry_after is not None else 30
        except ValueError:
            retry_after_seconds = 30
        return RateLimitedError(
            f"Jira rate-limited this request: {body_text[:500]}",
            retry_after_seconds=retry_after_seconds,
        )
    return UpstreamUnavailableError(f"Jira returned {status_code}: {body_text[:500]}")
