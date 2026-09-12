"""Named error conditions for the source-control MCP-shaped contract (F3).

Per master spec Section 7.6 / the F3 schema
(services/mcp-stubs/source-control/schema/source-control.schema.json), every
tool exposes exactly four named error conditions, each carrying a
`retryable` flag: `not-found` and `permission-denied` are never retryable
(retrying with the same input cannot succeed); `rate-limited` and
`upstream-unavailable` are transient and retryable.

This module deliberately does NOT invent a fifth "revoked" error code --
the schema's error `code` enum is closed to these four. A revoked App
installation surfaces as `permission-denied` (see
`github_client.map_http_error`), but is recorded distinctly in the audit
log (`audit.py`) as an access-revocation event, per spec Section 17.1's
"revocation is verified end-to-end ... a token revoked in name but still
honored somewhere is treated as a live incident."
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    NOT_FOUND = "not-found"
    PERMISSION_DENIED = "permission-denied"
    RATE_LIMITED = "rate-limited"
    UPSTREAM_UNAVAILABLE = "upstream-unavailable"


_RETRYABLE = {
    ErrorCode.NOT_FOUND: False,
    ErrorCode.PERMISSION_DENIED: False,
    ErrorCode.RATE_LIMITED: True,
    ErrorCode.UPSTREAM_UNAVAILABLE: True,
}


def is_retryable(code: ErrorCode) -> bool:
    return _RETRYABLE[code]


class SourceControlError(Exception):
    """Base class for every error the source-control service raises
    internally. Carries a named `code` (see `ErrorCode`). `service.py`
    catches these at the operation boundary and turns them into the
    error branch of the `Result` envelope -- callers never see a raw
    exception cross the service API."""

    code: ErrorCode

    def __init__(self, message: str, *, retry_after_seconds: int | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}

    @property
    def retryable(self) -> bool:
        return is_retryable(self.code)


class NotFoundError(SourceControlError):
    code = ErrorCode.NOT_FOUND


class PermissionDeniedError(SourceControlError):
    code = ErrorCode.PERMISSION_DENIED


class RateLimitedError(SourceControlError):
    code = ErrorCode.RATE_LIMITED


class UpstreamUnavailableError(SourceControlError):
    code = ErrorCode.UPSTREAM_UNAVAILABLE


class AccessRevokedError(PermissionDeniedError):
    """A PermissionDeniedError raised specifically because the GitHub App
    installation itself has been revoked/uninstalled (HTTP 401/403 with no
    rate-limit signal on what should be an authenticated installation
    call), as opposed to some other permission shortfall (e.g. repo not
    in this tenant's allow-list). Same wire error code
    (`permission-denied`, not retryable) -- the distinction is for the
    audit trail and for the caller-side test asserting revocation is
    treated as final, never retried as if it were transient."""
