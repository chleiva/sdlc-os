"""Named error conditions for the Registry Service's MCP-shaped contract.

Per master spec Section 7.6, every tool in the system exposes a
documented, named set of error conditions distinct from a successful
empty result, so a caller can tell "nothing matched" from "the tool
failed" without inspecting prose. Section 7.6 names four conditions that
apply platform-wide: not-found, permission-denied, rate-limited,
upstream-unavailable. F2's brief additionally requires two conditions
specific to the Registry Service's own state-machine and concurrency
rules: illegal-transition and stale-version. `invalid-input` covers
malformed/missing required fields, which every MCP tool needs regardless
of domain.

Tenant-scoping failures are deliberately NOT represented here as
`permission-denied` or any other error: per Section 14.13, a query with a
missing or wrong tenant_id must fail closed by returning an empty result,
never an error that would let a caller distinguish "wrong tenant" from
"no such run" (either one leaks the existence of another tenant's data).
See `service.py`'s `Result.empty()` for that path.
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    NOT_FOUND = "not-found"
    PERMISSION_DENIED = "permission-denied"
    RATE_LIMITED = "rate-limited"
    UPSTREAM_UNAVAILABLE = "upstream-unavailable"

    # Registry-Service-specific, additive to the Section 7.6 baseline.
    ILLEGAL_TRANSITION = "illegal-transition"
    STALE_VERSION = "stale-version"
    INVALID_INPUT = "invalid-input"


class RegistryError(Exception):
    """Base class for every error the Registry Service can raise.

    Carries a named `code` (see `ErrorCode`) and a human-readable
    `message`. `service.py` catches these at the operation boundary and
    turns them into the error branch of the `Result` envelope -- callers
    never see a raw exception cross the service API.
    """

    code: ErrorCode

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotFoundError(RegistryError):
    code = ErrorCode.NOT_FOUND


class PermissionDeniedError(RegistryError):
    code = ErrorCode.PERMISSION_DENIED


class RateLimitedError(RegistryError):
    code = ErrorCode.RATE_LIMITED


class UpstreamUnavailableError(RegistryError):
    code = ErrorCode.UPSTREAM_UNAVAILABLE


class IllegalTransitionError(RegistryError):
    code = ErrorCode.ILLEGAL_TRANSITION


class StaleVersionError(RegistryError):
    code = ErrorCode.STALE_VERSION


class InvalidInputError(RegistryError):
    code = ErrorCode.INVALID_INPUT
