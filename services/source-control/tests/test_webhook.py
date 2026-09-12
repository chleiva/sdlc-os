"""Real HMAC-SHA256 GitHub webhook signature verification (spec Section
17.3's webhook-authentication mechanism, applied here to GitHub App
installation events -- used to react to a real-world App
uninstall/suspend event)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from source_control.webhook import (
    WebhookVerificationError,
    is_installation_revocation_event,
    verify_signature,
)

SECRET = b"super-secret-webhook-key"


def _sign(payload: bytes, secret: bytes = SECRET) -> str:
    return "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted():
    payload = json.dumps({"action": "deleted", "installation": {"id": 42}}).encode()
    verify_signature(payload, _sign(payload), SECRET)  # must not raise


def test_missing_signature_header_is_rejected():
    payload = b'{"action": "deleted"}'
    with pytest.raises(WebhookVerificationError):
        verify_signature(payload, None, SECRET)


def test_tampered_payload_is_rejected():
    payload = b'{"action": "deleted"}'
    signature = _sign(payload)
    tampered = b'{"action": "created"}'
    with pytest.raises(WebhookVerificationError):
        verify_signature(tampered, signature, SECRET)


def test_wrong_secret_is_rejected():
    payload = b'{"action": "deleted"}'
    signature = _sign(payload, secret=b"a-different-secret")
    with pytest.raises(WebhookVerificationError):
        verify_signature(payload, signature, SECRET)


def test_unsupported_scheme_is_rejected():
    payload = b'{"action": "deleted"}'
    with pytest.raises(WebhookVerificationError):
        verify_signature(payload, "sha1=deadbeef", SECRET)


def test_installation_deleted_event_is_recognized_as_revocation():
    assert is_installation_revocation_event("installation", {"action": "deleted"})


def test_installation_suspend_event_is_recognized_as_revocation():
    assert is_installation_revocation_event("installation", {"action": "suspend"})


def test_installation_created_event_is_not_a_revocation():
    assert not is_installation_revocation_event("installation", {"action": "created"})


def test_unrelated_event_type_is_not_a_revocation():
    assert not is_installation_revocation_event("pull_request", {"action": "deleted"})
