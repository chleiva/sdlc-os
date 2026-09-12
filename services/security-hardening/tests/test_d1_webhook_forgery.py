"""D10 adversarial pass on D1 (job dispatcher) / Sec. 17.3 webhook auth.

The existing job-dispatcher suite (`test_webhook_auth_gate.py`) already
proves, with a call-counter spy, that tenant resolution never runs
before auth passes. This file does NOT re-run that proof; it tries to
*break* the HMAC scheme itself with real forgery attempts: reusing a
valid signature over a different (timestamp, body) pair, splicing a
signature computed under one tenant's secret onto another tenant's
claimed identity, and boundary/precision edge cases the existing suite
doesn't cover. Everything here drives the REAL `issue_tracker.webhook_signing`
verifier and the REAL `job_dispatcher.webhook_auth`/`dispatcher` code --
nothing is mocked except the downstream Registry/capacity/Jira stubs,
which are poisoned (raise if ever called) so a bypass would be loudly
visible, not silently swallowed.
"""
from __future__ import annotations

import json
import time

import pytest

from issue_tracker import webhook_signing
from job_dispatcher.dispatcher import JobDispatcher
from job_dispatcher.tenant_resolution import TenantDirectory
from job_dispatcher.webhook_auth import AuthenticationError, authenticate, require_authenticated, AuthenticatedTrigger, RejectedTrigger

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
SECRET_A = b"tenant-a-real-secret-0123456789"
SECRET_B = b"tenant-b-real-secret-9876543210"


def _secret_lookup(tenant_id: str) -> bytes | None:
    return {TENANT_A: SECRET_A, TENANT_B: SECRET_B}.get(tenant_id)


def _headers_for(tenant_id: str, secret: bytes, body: bytes, ts: str) -> dict:
    sig = webhook_signing.compute_signature(secret, ts, body)
    return {
        webhook_signing.HEADER_TENANT_ID: tenant_id,
        webhook_signing.HEADER_TIMESTAMP: ts,
        webhook_signing.HEADER_SIGNATURE: sig,
    }


class _NeverCallMe(Exception):
    pass


def _poisoned_dispatcher():
    class PoisonedRegistry:
        def create_run(self, **kwargs):
            raise _NeverCallMe("registry.create_run must never run on a forged/rejected request")

    class PoisonedCapacity:
        def request_capacity(self, tenant_id):
            raise _NeverCallMe("capacity_provider must never run on a forged/rejected request")

    def _poisoned_jira(tenant_id):
        raise _NeverCallMe("jira_client_for_tenant must never run on a forged/rejected request")

    return JobDispatcher(
        secret_lookup=_secret_lookup,
        tenant_directory=TenantDirectory.from_mapping({"PROJA": TENANT_A, "PROJB": TENANT_B}),
        registry=PoisonedRegistry(),
        capacity_provider=PoisonedCapacity(),
        jira_client_for_tenant=_poisoned_jira,
    )


# ---------------------------------------------------------------------
# Real forgery attempt #1: replay a valid signature over a NEW body.
# The signature covers exactly "timestamp.body" -- an attacker who
# observed one valid (ts, body, sig) triple must not be able to keep ts
# and sig fixed while swapping in a different, more damaging body.
# ---------------------------------------------------------------------
def test_valid_signature_does_not_transfer_to_a_different_body():
    now = time.time()
    ts = str(int(now))
    original_body = json.dumps({"tenant_id": TENANT_A, "issue_key": "PROJA-1"}, sort_keys=True).encode()
    sig = webhook_signing.compute_signature(SECRET_A, ts, original_body)

    forged_body = json.dumps({"tenant_id": TENANT_A, "issue_key": "PROJA-999-ADMIN"}, sort_keys=True).encode()
    headers = {
        webhook_signing.HEADER_TENANT_ID: TENANT_A,
        webhook_signing.HEADER_TIMESTAMP: ts,
        webhook_signing.HEADER_SIGNATURE: sig,  # signature is for original_body, not forged_body
    }
    result = webhook_signing.verify_request(headers=headers, body=forged_body, secret_lookup=_secret_lookup, now=now)
    assert result.ok is False
    assert result.reject_reason == webhook_signing.RejectReason.SIGNATURE_MISMATCH


# ---------------------------------------------------------------------
# Real forgery attempt #2: replay a valid signature under a NEW
# timestamp (keeping the same body). The canonical message is
# timestamp + "." + body, so shifting the timestamp while replaying an
# old signature must also fail, even if the new timestamp is itself
# within the replay window.
# ---------------------------------------------------------------------
def test_valid_signature_does_not_transfer_to_a_different_timestamp():
    body = json.dumps({"tenant_id": TENANT_A, "issue_key": "PROJA-1"}, sort_keys=True).encode()
    ts1 = str(int(time.time()) - 100)
    sig_for_ts1 = webhook_signing.compute_signature(SECRET_A, ts1, body)

    ts2 = str(int(time.time()))  # different, still-fresh timestamp
    headers = {
        webhook_signing.HEADER_TENANT_ID: TENANT_A,
        webhook_signing.HEADER_TIMESTAMP: ts2,
        webhook_signing.HEADER_SIGNATURE: sig_for_ts1,  # signed for ts1, presented with ts2
    }
    result = webhook_signing.verify_request(headers=headers, body=body, secret_lookup=_secret_lookup, now=int(ts2))
    assert result.ok is False
    assert result.reject_reason == webhook_signing.RejectReason.SIGNATURE_MISMATCH


# ---------------------------------------------------------------------
# Real forgery attempt #3: cross-tenant splice. Sign a request with
# tenant B's real secret, then claim tenant_id=tenant-a in the header
# (or vice versa). The verifier must key its HMAC check off the
# *claimed* tenant_id's own secret, so a signature valid under B's
# secret must not validate under a request claiming to be A.
# ---------------------------------------------------------------------
def test_signature_computed_under_tenant_b_secret_is_rejected_when_claiming_tenant_a():
    now = time.time()
    ts = str(int(now))
    body = json.dumps({"tenant_id": TENANT_A, "issue_key": "PROJA-1"}, sort_keys=True).encode()

    # Attacker knows/controls tenant B's secret (e.g. a compromised
    # tenant B integration) and tries to forge a request against tenant A.
    forged_sig = webhook_signing.compute_signature(SECRET_B, ts, body)
    headers = {
        webhook_signing.HEADER_TENANT_ID: TENANT_A,  # claims to be tenant A
        webhook_signing.HEADER_TIMESTAMP: ts,
        webhook_signing.HEADER_SIGNATURE: forged_sig,  # but signed with tenant B's secret
    }
    result = webhook_signing.verify_request(headers=headers, body=body, secret_lookup=_secret_lookup, now=now)
    assert result.ok is False
    assert result.reject_reason == webhook_signing.RejectReason.SIGNATURE_MISMATCH


# ---------------------------------------------------------------------
# Real forgery attempt #4: the concatenation-collision angle. Since
# `_canonical_message` is `timestamp + "." + body` via straight byte
# concatenation of the *actual header value* and the *actual body*
# (never a single joined string that's re-split), an attacker cannot
# make (ts="1", body=".2xyz") collide with (ts="1.2", body="xyz") --
# confirm this explicitly, since it would be a real ambiguity if the
# verifier ever re-parsed a joined string instead of reconstructing from
# the two separate parts.
# ---------------------------------------------------------------------
def test_no_timestamp_body_boundary_collision_forgery():
    # A "legitimate" request: ts="1700000000", body=b"abc"
    ts_legit = "1700000000"
    body_legit = b"abc"
    sig_legit = webhook_signing.compute_signature(SECRET_A, ts_legit, body_legit)

    # An attacker tries to claim a DIFFERENT split of the same bytes
    # that would produce an identical canonical message if the verifier
    # ever concatenated first and re-split (it doesn't -- but prove the
    # headers-as-given still don't validate under a shifted split).
    ts_attempt = "1700000000.ab"  # shifted the "." boundary into the timestamp header itself
    body_attempt = b"c"
    headers = {
        webhook_signing.HEADER_TENANT_ID: TENANT_A,
        webhook_signing.HEADER_TIMESTAMP: ts_attempt,
        webhook_signing.HEADER_SIGNATURE: sig_legit,
    }
    result = webhook_signing.verify_request(headers=headers, body=body_attempt, secret_lookup=_secret_lookup, now=1700000000)
    assert result.ok is False
    # Either malformed-timestamp (int() rejects "1700000000.ab") or a
    # signature mismatch -- both are fail-closed; what must NEVER happen
    # is `ok is True`.
    assert result.reject_reason in (
        webhook_signing.RejectReason.MALFORMED_TIMESTAMP,
        webhook_signing.RejectReason.SIGNATURE_MISMATCH,
    )


# ---------------------------------------------------------------------
# Replay-window exact boundary (only 299/301 were tested upstream;
# nail down the documented "five minutes" == 300 exactly).
# ---------------------------------------------------------------------
def test_replay_window_is_inclusive_at_exactly_300_seconds():
    ts = str(int(time.time()) - 300)
    body = b'{"x":1}'
    headers = _headers_for(TENANT_A, SECRET_A, body, ts)
    result = webhook_signing.verify_request(headers=headers, body=body, secret_lookup=_secret_lookup, now=int(ts) + 300)
    assert result.ok is True  # exactly 300s: "> 300" is the reject condition, so 300 itself must pass


def test_replay_window_rejects_a_future_timestamp_beyond_the_window_too():
    """abs(current - ts) > window rejects a timestamp from the FUTURE
    just as much as a stale one -- an attacker pre-signing a request for
    a future timestamp to extend its usable window must not work."""
    future_ts = str(int(time.time()) + 301)
    body = b'{"x":1}'
    headers = _headers_for(TENANT_A, SECRET_A, body, future_ts)
    result = webhook_signing.verify_request(headers=headers, body=body, secret_lookup=_secret_lookup, now=int(future_ts) - 301)
    assert result.ok is False
    assert result.reject_reason == webhook_signing.RejectReason.STALE_TIMESTAMP


# ---------------------------------------------------------------------
# Full end-to-end forgery attempt through the real JobDispatcher: even
# with poisoned (raise-on-call) downstreams, a forged cross-tenant
# splice must be rejected at the auth gate, never reaching capacity/
# registry/Jira code.
# ---------------------------------------------------------------------
def test_end_to_end_cross_tenant_splice_never_reaches_dispatch(monkeypatch):
    import job_dispatcher.dispatcher as dispatcher_module

    calls = []
    real_require_resolved = dispatcher_module.require_resolved

    def spy(*, trigger, directory):
        calls.append(trigger)
        return real_require_resolved(trigger=trigger, directory=directory)

    monkeypatch.setattr(dispatcher_module, "require_resolved", spy)

    dispatcher = _poisoned_dispatcher()
    now = time.time()
    ts = str(int(now))
    body = json.dumps({"tenant_id": TENANT_A, "issue_key": "PROJA-1", "project_key": "PROJA", "repository": "org/repo"}, sort_keys=True).encode()
    forged_sig = webhook_signing.compute_signature(SECRET_B, ts, body)  # tenant B's secret, claiming tenant A
    headers = {
        webhook_signing.HEADER_TENANT_ID: TENANT_A,
        webhook_signing.HEADER_TIMESTAMP: ts,
        webhook_signing.HEADER_SIGNATURE: forged_sig,
    }
    with pytest.raises(AuthenticationError) as exc_info:
        dispatcher.handle_webhook(headers=headers, body=body)
    assert exc_info.value.rejected.reject_reason == "signature-mismatch"
    assert calls == [], "a forged cross-tenant request must never reach tenant resolution"
