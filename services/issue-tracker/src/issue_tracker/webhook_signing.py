"""Per-tenant HMAC webhook signing/verification (master spec Sec. 17.3).

    "Webhook authentication: the Section 4.4 Jira Automation web
    request, and any equivalent external trigger, is authenticated with
    a per-tenant HMAC signature over the request body plus a timestamp,
    verified by the job dispatcher before tenant resolution (Section
    14.13) is even attempted, with a bounded replay window (five
    minutes) -- an unsigned or stale-timestamped request is rejected
    before it can consume any capacity-provisioning logic, not merely
    logged and ignored."

This is fully implementable and testable here: both the signer (D4's
side, in `webhook_relay.py`) and the verifier (standing in for D1's
side, since D1 does not exist yet -- see its docstring for the exact
boundary this stands in for) are our own code.

A note on "before tenant resolution": the dispatcher must know *which*
tenant's key to verify against before it can verify anything, so the
tenant_id itself has to be readable pre-verification (carried as a
plain header here, `X-SDLC-Tenant-Id` -- never trusted for anything
*except* selecting which key to check the signature against). What
"before tenant resolution" means concretely is: looking up a per-tenant
HMAC key is a cheap, constant-shape map lookup, never the heavier
tenant-resolution path of Section 14.13 (routing to the tenant's
compute cell, provisioning capacity, spending budget) -- that heavier
path must never run until *after* `verify_request` below returns ok.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import time
from dataclasses import dataclass
from typing import Callable

REPLAY_WINDOW_SECONDS = 300  # five minutes, per Sec. 17.3

HEADER_TENANT_ID = "X-SDLC-Tenant-Id"
HEADER_TIMESTAMP = "X-SDLC-Timestamp"
HEADER_SIGNATURE = "X-SDLC-Signature"


def _canonical_message(timestamp: str, body: bytes) -> bytes:
    # Signing over "timestamp.body" (a fixed separator byte the
    # timestamp, which is always digits, cannot itself contain) is the
    # standard construction (Stripe/GitHub-style) that prevents an
    # attacker from re-using a valid signature for a different
    # timestamp or a different body independently.
    return timestamp.encode("ascii") + b"." + body


def compute_signature(secret: bytes, timestamp: str, body: bytes) -> str:
    mac = _hmac.new(secret, _canonical_message(timestamp, body), hashlib.sha256)
    return mac.hexdigest()


def sign_request(*, tenant_id: str, secret: bytes, body: bytes, now: float | None = None) -> dict[str, str]:
    """Build the headers a Jira-Automation-triggered dispatch request
    carries. Returns a plain header dict; callers attach these to the
    outgoing HTTP POST to the job dispatcher (D1) endpoint.
    """
    timestamp = str(int(now if now is not None else time.time()))
    signature = compute_signature(secret, timestamp, body)
    return {
        HEADER_TENANT_ID: tenant_id,
        HEADER_TIMESTAMP: timestamp,
        HEADER_SIGNATURE: signature,
    }


class RejectReason:
    MISSING_TENANT_ID = "missing-tenant-id"
    MISSING_TIMESTAMP = "missing-timestamp"
    MISSING_SIGNATURE = "missing-signature"
    MALFORMED_TIMESTAMP = "malformed-timestamp"
    STALE_TIMESTAMP = "stale-timestamp"
    UNKNOWN_TENANT = "unknown-tenant"
    SIGNATURE_MISMATCH = "signature-mismatch"


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    tenant_id: str | None = None
    reject_reason: str | None = None
    detail: str | None = None


def verify_request(
    *,
    headers: dict[str, str],
    body: bytes,
    secret_lookup: Callable[[str], bytes | None],
    now: float | None = None,
    replay_window_seconds: int = REPLAY_WINDOW_SECONDS,
) -> VerifyResult:
    """Verify a webhook request per Sec. 17.3, fail-closed at every
    step. `secret_lookup(tenant_id)` returns that tenant's HMAC key, or
    None if the tenant_id is unrecognized -- deliberately a *cheap*
    lookup (a dict/keyring read), never the full tenant-resolution
    path; this function must return before anything resembling
    capacity provisioning runs.

    Header lookups are case-insensitive-safe by normalizing keys, since
    HTTP header casing is not guaranteed to round-trip.
    """
    normalized = {k.lower(): v for k, v in headers.items()}

    tenant_id = normalized.get(HEADER_TENANT_ID.lower())
    if not tenant_id:
        return VerifyResult(ok=False, reject_reason=RejectReason.MISSING_TENANT_ID)

    timestamp = normalized.get(HEADER_TIMESTAMP.lower())
    if not timestamp:
        return VerifyResult(ok=False, tenant_id=tenant_id, reject_reason=RejectReason.MISSING_TIMESTAMP)

    signature = normalized.get(HEADER_SIGNATURE.lower())
    if not signature:
        return VerifyResult(ok=False, tenant_id=tenant_id, reject_reason=RejectReason.MISSING_SIGNATURE)

    try:
        ts_value = int(timestamp)
    except ValueError:
        return VerifyResult(ok=False, tenant_id=tenant_id, reject_reason=RejectReason.MALFORMED_TIMESTAMP)

    current = now if now is not None else time.time()
    if abs(current - ts_value) > replay_window_seconds:
        return VerifyResult(
            ok=False,
            tenant_id=tenant_id,
            reject_reason=RejectReason.STALE_TIMESTAMP,
            detail=f"timestamp {ts_value} is outside the {replay_window_seconds}s replay window (now={current:.0f})",
        )

    secret = secret_lookup(tenant_id)
    if secret is None:
        # Deliberately checked *after* the timestamp check (cheaper,
        # and avoids giving a timing signal about which tenant_ids
        # exist via a longer code path for unknown ones), but still
        # strictly before any signature comparison.
        return VerifyResult(ok=False, tenant_id=tenant_id, reject_reason=RejectReason.UNKNOWN_TENANT)

    expected = compute_signature(secret, timestamp, body)
    if not _hmac.compare_digest(expected, signature):
        return VerifyResult(ok=False, tenant_id=tenant_id, reject_reason=RejectReason.SIGNATURE_MISMATCH)

    return VerifyResult(ok=True, tenant_id=tenant_id)
