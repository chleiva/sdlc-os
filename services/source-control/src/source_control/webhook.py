"""GitHub webhook signature verification (real HMAC verification, per
spec Section 17.3's webhook-authentication mechanism applied to GitHub
App events -- the same discipline the spec states explicitly for the
Jira Automation webhook: verified before anything downstream is
attempted, on a bounded timestamp window, rejected outright rather than
merely logged).

GitHub signs every webhook delivery body with HMAC-SHA256 over the raw
request bytes, keyed by the App's webhook secret, and sends it as
`X-Hub-Signature-256: sha256=<hex digest>`. This is what lets this
service trust an "installation deleted/suspended" event (used to react
to real-world App revocation) without trusting the network path or the
sender's IP.
"""

from __future__ import annotations

import hashlib
import hmac


class WebhookVerificationError(Exception):
    pass


def verify_signature(payload: bytes, signature_header: str | None, webhook_secret: bytes) -> None:
    """Raises WebhookVerificationError unless `signature_header` is a
    valid `sha256=<hex>` HMAC-SHA256 of `payload` keyed by
    `webhook_secret`. Constant-time compare (`hmac.compare_digest`) so
    this check itself cannot be timed to leak the secret."""
    if not signature_header:
        raise WebhookVerificationError("missing X-Hub-Signature-256 header")
    if not signature_header.startswith("sha256="):
        raise WebhookVerificationError("unsupported signature scheme (expected sha256=...)")
    provided_hex = signature_header[len("sha256="):]
    expected = hmac.new(webhook_secret, payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided_hex, expected):
        raise WebhookVerificationError("signature does not match payload")


def is_installation_revocation_event(event_type: str, payload: dict) -> bool:
    """True for the two GitHub App installation webhook events that mean
    "this installation's access just ended": the App was uninstalled
    entirely (`deleted`), or an org admin paused it without uninstalling
    (`suspend`). Both are treated identically by this service: as access
    revoked for that installation_id, per the D5 acceptance criterion
    that uninstallation is the actual revocation mechanism and must be
    exercised, not assumed."""
    if event_type != "installation":
        return False
    return payload.get("action") in ("deleted", "suspend")
