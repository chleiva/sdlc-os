"""Sec. 17.3's webhook-authentication gate, structured so it is
architecturally impossible for tenant resolution or capacity logic to
run before it passes.

    "the Section 4.4 Jira Automation web request... is authenticated
    with a per-tenant HMAC signature over the request body plus a
    timestamp, verified by the job dispatcher before tenant resolution
    is even attempted, with a bounded replay window (five minutes) --
    an unsigned or stale-timestamped request is rejected before it can
    consume any capacity-provisioning logic."  (master spec Sec. 17.3)

We do not reimplement the HMAC scheme here: `issue_tracker.webhook_signing`
is D4's real, tested implementation of exactly this scheme, and its own
docstring says it is "standing in for D1's side, since D1 does not exist
yet" -- D1 (this package) is that consumer now, so we import it as a
real dependency rather than duplicating it (the repo's own ground rule:
never let two components quietly diverge on a shared mechanism).

The "architecturally impossible" part is the nominal-type pattern
(mirroring D4's `gating.OptedInStory` / D4's own description of D5's
pattern): `AuthenticatedTrigger` has no public constructor other than
`authenticate()`. Every downstream function in this package --
tenant resolution, capacity requests, Run creation -- takes an
`AuthenticatedTrigger` as its first argument, not raw headers/body, so
a caller literally cannot reach that code without first obtaining one
from a passing `authenticate()` call. There is no second code path that
skips the check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from issue_tracker import webhook_signing


@dataclass(frozen=True)
class AuthenticatedTrigger:
    """Proof that a specific webhook request passed Sec. 17.3
    authentication at the moment it was checked.

    The only way to obtain one is `authenticate()` returning it; there
    is no other constructor exported from this module. `tenant_id`
    here is the header-carried claim that selected which HMAC key was
    checked -- Sec. 4.4 tenant *resolution* (confirming/deriving the
    tenant from the Jira project mapping) is a separate, later step
    that re-derives tenant identity independently rather than trusting
    this claim outright (see `tenant_resolution.py`).
    """

    claimed_tenant_id: str
    body: bytes

    def body_json(self) -> dict:
        import json

        return json.loads(self.body.decode("utf-8"))


@dataclass(frozen=True)
class RejectedTrigger:
    """The negative outcome, carrying why -- never silently a bare
    False/None, so a caller (and a test) can assert on the specific
    reason without inspecting an HTTP status code."""

    reject_reason: str
    detail: str | None = None
    claimed_tenant_id: str | None = None


class AuthenticationError(Exception):
    """Raised by `require_authenticated` when a request fails Sec.
    17.3 verification. The only exception type the HTTP layer treats
    as "reject with 401 before touching anything else"."""

    def __init__(self, rejected: RejectedTrigger):
        super().__init__(f"{rejected.reject_reason}: {rejected.detail}")
        self.rejected = rejected


def authenticate(
    *,
    headers: dict[str, str],
    body: bytes,
    secret_lookup: Callable[[str], bytes | None],
    now: float | None = None,
    replay_window_seconds: int = webhook_signing.REPLAY_WINDOW_SECONDS,
) -> AuthenticatedTrigger | RejectedTrigger:
    """The single choke point every incoming webhook must pass through.

    Delegates the actual HMAC-over-body+timestamp check, with the
    five-minute replay window, to D4's real `webhook_signing.verify_request`
    -- this function adds nothing to the cryptographic check itself, only
    the nominal-type wrapper that makes "authenticated" a value, not a
    boolean a caller could ignore.
    """
    result = webhook_signing.verify_request(
        headers=headers,
        body=body,
        secret_lookup=secret_lookup,
        now=now,
        replay_window_seconds=replay_window_seconds,
    )
    if not result.ok:
        return RejectedTrigger(
            reject_reason=result.reject_reason or "unknown",
            detail=result.detail,
            claimed_tenant_id=result.tenant_id,
        )
    assert result.tenant_id is not None
    return AuthenticatedTrigger(claimed_tenant_id=result.tenant_id, body=body)


def require_authenticated(
    *,
    headers: dict[str, str],
    body: bytes,
    secret_lookup: Callable[[str], bytes | None],
    now: float | None = None,
    replay_window_seconds: int = webhook_signing.REPLAY_WINDOW_SECONDS,
) -> AuthenticatedTrigger:
    """Same check as `authenticate`, but raises instead of returning the
    negative branch. This is the function every real call site (the HTTP
    handler in `http_app.py`) uses -- it is structurally impossible to
    fall through to tenant resolution / capacity code on a rejected
    request, because `AuthenticationError` unwinds the stack before an
    `AuthenticatedTrigger` value ever comes into existence.
    """
    outcome = authenticate(
        headers=headers,
        body=body,
        secret_lookup=secret_lookup,
        now=now,
        replay_window_seconds=replay_window_seconds,
    )
    if isinstance(outcome, RejectedTrigger):
        raise AuthenticationError(outcome)
    return outcome
