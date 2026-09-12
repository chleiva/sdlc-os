"""Builds the three mutually-exclusive result envelopes every tool in this
system returns (spec Sec. 7.6): a success result, a distinct empty result,
and a named error result. See services/mcp-stubs/README.md for the full
shape rationale.
"""
from __future__ import annotations

from typing import Any

from .scenarios import ERROR_CONDITIONS

_ERROR_MESSAGES: dict[str, str] = {
    "not-found": "The requested resource does not exist, or is not visible to this tenant.",
    "permission-denied": "The caller's credential does not grant access to this resource.",
    "rate-limited": "The upstream is rate-limiting this tenant. Retry after the given backoff.",
    "upstream-unavailable": "The upstream service is temporarily unavailable.",
}

# Whether a caller should retry at all. not-found/permission-denied are not
# retryable (retrying with the same input cannot succeed); rate-limited and
# upstream-unavailable are transient and retryable.
_RETRYABLE: dict[str, bool] = {
    "not-found": False,
    "permission-denied": False,
    "rate-limited": True,
    "upstream-unavailable": True,
}


def ok(data: dict[str, Any]) -> dict[str, Any]:
    """A successful result carrying data. Always has outcome == 'ok' and a
    'data' object -- never conflatable with empty (no 'data') or error (no
    'error')."""
    return {"outcome": "ok", "data": data}


def empty(reason: str, **extra: Any) -> dict[str, Any]:
    """A successful call that had nothing to return. Always has outcome ==
    'empty' and a 'reason' string; never has 'data' or 'error', so a caller
    can never mistake it for either."""
    return {"outcome": "empty", "reason": reason, **extra}


def error(
    code: str,
    message: str | None = None,
    retry_after_seconds: int | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One of the four named error conditions (spec Sec. 7.6). Always has
    outcome == 'error' and a non-null 'error' object -- never conflatable
    with a successful empty result."""
    if code not in ERROR_CONDITIONS:
        raise ValueError(f"Unknown error condition {code!r}; must be one of {ERROR_CONDITIONS}")
    err: dict[str, Any] = {
        "code": code,
        "message": message or _ERROR_MESSAGES[code],
        "retryable": _RETRYABLE[code],
        "retry_after_seconds": (retry_after_seconds if retry_after_seconds is not None else 30)
        if code == "rate-limited"
        else None,
    }
    if details:
        err["details"] = details
    return {"outcome": "error", "error": err}
