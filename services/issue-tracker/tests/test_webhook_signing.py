"""Sec. 17.3's per-tenant HMAC webhook-signing scheme, fully
implemented and tested locally (both signer and verifier are our own
code) -- no live Jira/Atlassian dependency needed for this piece.
"""

import time

from issue_tracker.webhook_signing import RejectReason, sign_request, verify_request

SECRET_A = b"tenant-a-secret-key-0123456789"
SECRET_B = b"tenant-b-secret-key-9876543210"


def _secret_lookup(secrets: dict[str, bytes]):
    def lookup(tenant_id: str) -> bytes | None:
        return secrets.get(tenant_id)

    return lookup


def test_valid_signature_verifies_ok():
    body = b'{"issue_key":"PROJ-1","tenant_id":"tenant-a"}'
    headers = sign_request(tenant_id="tenant-a", secret=SECRET_A, body=body)
    result = verify_request(headers=headers, body=body, secret_lookup=_secret_lookup({"tenant-a": SECRET_A}))
    assert result.ok
    assert result.tenant_id == "tenant-a"


def test_tampered_body_fails_signature_check():
    body = b'{"issue_key":"PROJ-1","tenant_id":"tenant-a"}'
    headers = sign_request(tenant_id="tenant-a", secret=SECRET_A, body=body)
    tampered_body = b'{"issue_key":"PROJ-999","tenant_id":"tenant-a"}'
    result = verify_request(headers=headers, body=tampered_body, secret_lookup=_secret_lookup({"tenant-a": SECRET_A}))
    assert not result.ok
    assert result.reject_reason == RejectReason.SIGNATURE_MISMATCH


def test_wrong_tenants_secret_fails_signature_check():
    """A signature computed under tenant A's key must not verify
    against tenant B's request -- the whole point of *per-tenant* keys
    (Sec. 17.3), never a shared platform-wide secret."""
    body = b'{"issue_key":"PROJ-1","tenant_id":"tenant-b"}'
    # sign as if it were tenant-a's secret, but claim to be tenant-b
    headers = sign_request(tenant_id="tenant-b", secret=SECRET_A, body=body)
    result = verify_request(headers=headers, body=body,
                             secret_lookup=_secret_lookup({"tenant-a": SECRET_A, "tenant-b": SECRET_B}))
    assert not result.ok
    assert result.reject_reason == RejectReason.SIGNATURE_MISMATCH


def test_stale_timestamp_rejected_outside_replay_window():
    body = b'{"issue_key":"PROJ-1"}'
    old_timestamp = time.time() - 301  # just past the 5-minute window
    headers = sign_request(tenant_id="tenant-a", secret=SECRET_A, body=body, now=old_timestamp)
    result = verify_request(headers=headers, body=body, secret_lookup=_secret_lookup({"tenant-a": SECRET_A}))
    assert not result.ok
    assert result.reject_reason == RejectReason.STALE_TIMESTAMP


def test_timestamp_within_replay_window_is_accepted():
    body = b'{"issue_key":"PROJ-1"}'
    recent_timestamp = time.time() - 200  # inside the 5-minute window
    headers = sign_request(tenant_id="tenant-a", secret=SECRET_A, body=body, now=recent_timestamp)
    result = verify_request(headers=headers, body=body, secret_lookup=_secret_lookup({"tenant-a": SECRET_A}))
    assert result.ok


def test_unsigned_request_missing_all_headers_is_rejected():
    result = verify_request(headers={}, body=b"{}", secret_lookup=_secret_lookup({}))
    assert not result.ok
    assert result.reject_reason == RejectReason.MISSING_TENANT_ID


def test_missing_signature_header_is_rejected():
    body = b"{}"
    headers = sign_request(tenant_id="tenant-a", secret=SECRET_A, body=body)
    del headers["X-SDLC-Signature"]
    result = verify_request(headers=headers, body=body, secret_lookup=_secret_lookup({"tenant-a": SECRET_A}))
    assert not result.ok
    assert result.reject_reason == RejectReason.MISSING_SIGNATURE


def test_unknown_tenant_is_rejected_without_leaking_which_tenants_exist():
    body = b"{}"
    headers = sign_request(tenant_id="tenant-ghost", secret=b"whatever", body=body)
    result = verify_request(headers=headers, body=body, secret_lookup=_secret_lookup({"tenant-a": SECRET_A}))
    assert not result.ok
    assert result.reject_reason == RejectReason.UNKNOWN_TENANT


def test_header_lookup_is_case_insensitive():
    body = b"{}"
    headers = sign_request(tenant_id="tenant-a", secret=SECRET_A, body=body)
    lowered = {k.lower(): v for k, v in headers.items()}
    result = verify_request(headers=lowered, body=body, secret_lookup=_secret_lookup({"tenant-a": SECRET_A}))
    assert result.ok
