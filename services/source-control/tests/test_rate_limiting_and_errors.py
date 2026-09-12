"""Real HTTP-status-driven error mapping against the mock server: a
rate-limited response is retryable, distinct from the not-retryable
revoked/permission-denied case (test_revocation.py) and from
upstream-unavailable (5xx). Exercises the F3 contract's four named error
conditions end-to-end through the real client."""

from __future__ import annotations

from source_control.errors import NotFoundError, RateLimitedError, UpstreamUnavailableError
from source_control.github_client import GitHubAppClient
import pytest


def test_rate_limited_response_is_retryable_and_carries_retry_after(github_client: GitHubAppClient, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "main")] = "a" * 40
    server.state.force_rate_limit_next = 1
    server.state.rate_limit_retry_after = 7

    with pytest.raises(RateLimitedError) as exc_info:
        github_client.get_ref(installation_id="inst-1", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")

    assert exc_info.value.retryable is True
    assert exc_info.value.retry_after_seconds == 7

    # And it was transient: the very next call (no more injected failures
    # queued) succeeds.
    result = github_client.get_ref(installation_id="inst-1", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")
    assert result["object"]["sha"] == "a" * 40


def test_upstream_5xx_is_upstream_unavailable_and_retryable(github_client: GitHubAppClient, mock_github):
    server, _ = mock_github
    server.state.refs[("acme", "app", "main")] = "a" * 40
    server.state.force_5xx_next = 1

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        github_client.get_ref(installation_id="inst-1", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")

    assert exc_info.value.retryable is True

    result = github_client.get_ref(installation_id="inst-1", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")
    assert result["object"]["sha"] == "a" * 40


def test_missing_ref_is_not_found_and_not_retryable(github_client: GitHubAppClient):
    with pytest.raises(NotFoundError) as exc_info:
        github_client.get_ref(installation_id="inst-1", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/does-not-exist")
    assert exc_info.value.retryable is False


def test_unreachable_host_is_upstream_unavailable():
    """A connection-level failure (host down / DNS failure / timeout) --
    not just an HTTP error status -- must also map to upstream-unavailable,
    never crash the caller with a raw socket exception."""
    from source_control.audit import AuditLogger
    from source_control.github_client import AppCredentials, GitHubAppClient
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    client = GitHubAppClient(
        credentials=AppCredentials(app_id="1", app_slug="sdlc-auto", private_key_pem=pem),
        api_base_url="http://127.0.0.1:1",  # nothing listens here
        audit_logger=AuditLogger(),
        timeout_seconds=2.0,
    )
    with pytest.raises(UpstreamUnavailableError) as exc_info:
        client.get_ref(installation_id="inst-1", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")
    assert exc_info.value.retryable is True
