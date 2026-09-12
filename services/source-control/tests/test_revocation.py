"""D5 acceptance criterion: "Uninstalling the App from a repository
actually revokes access -- tested, not assumed." We cannot literally
uninstall a real GitHub App in this environment (no live org), so this
tests the equivalent at the code boundary the brief calls out: the mock
server is made to behave exactly as GitHub does for a revoked/uninstalled
installation (401/403, no rate-limit signal), and we prove the client:

  1. treats it as `permission-denied`, not `rate-limited` or
     `upstream-unavailable` -- i.e. NOT a transient/retryable condition;
  2. never silently retries the call itself;
  3. evicts any cached installation token so a subsequent call re-checks
     rather than reusing a token GitHub has already invalidated;
  4. records the event in the audit trail distinctly, so a human
     reviewing NHI access (spec Section 17.1) can see "access ended"
     rather than "a blip."
"""

from __future__ import annotations

import pytest

from source_control.errors import PermissionDeniedError
from source_control.github_client import GitHubAppClient


def test_api_call_against_a_revoked_installation_raises_permission_denied_not_retryable(
    github_client: GitHubAppClient, mock_github,
):
    server, _ = mock_github
    server.state.revoked_installations.add("inst-revoked")
    server.state.refs[("acme", "app", "main")] = "a" * 40  # would succeed if access were live

    with pytest.raises(PermissionDeniedError) as exc_info:
        github_client.get_ref(installation_id="inst-revoked", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")

    assert exc_info.value.retryable is False


def test_token_exchange_itself_fails_for_a_revoked_installation(github_client: GitHubAppClient, mock_github):
    server, _ = mock_github
    server.state.revoked_installations.add("inst-revoked")

    with pytest.raises(PermissionDeniedError):
        github_client.create_installation_token("inst-revoked")


def test_revocation_is_never_retried_by_the_client_itself(github_client: GitHubAppClient, mock_github):
    """The client must make exactly one attempt against a revoked
    installation -- treating 401/403 as final, never looping/backing off
    as it would for rate-limited/upstream-unavailable."""
    server, _ = mock_github
    server.state.revoked_installations.add("inst-revoked")

    calls_before = len(server.state.call_log)
    with pytest.raises(PermissionDeniedError):
        github_client.get_ref(installation_id="inst-revoked", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")
    calls_after = len(server.state.call_log)

    assert calls_after - calls_before == 1, "a revoked installation must not trigger any client-side retry"


def test_previously_cached_token_is_evicted_once_the_installation_is_revoked(
    github_client: GitHubAppClient, mock_github,
):
    """Simulates the realistic sequence: token issued while the
    installation was live, then the App is uninstalled mid-session (the
    mock flips the installation to revoked without the client's
    knowledge, exactly as would happen for a real uninstall event
    arriving asynchronously). The next authenticated call must surface
    permission-denied and must not keep silently reusing the
    now-invalid cached token for a third call once eviction has
    happened."""
    server, _ = mock_github

    # Token issued while installation is live.
    token_before = github_client.token_cache.get_token("inst-x")
    assert "inst-x" in server.state.issued_tokens.values()

    # Now the org admin uninstalls the App (or GitHub invalidates the
    # token for any other reason) -- the mock represents this exactly as
    # GitHub would: the specific installation starts getting 401s.
    server.state.revoked_installations.add("inst-x")
    server.state.refs[("acme", "app", "main")] = "b" * 40

    with pytest.raises(PermissionDeniedError):
        github_client.get_ref(installation_id="inst-x", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")

    # The cache must have evicted the now-dead token rather than holding
    # onto it indefinitely.
    with github_client.token_cache._lock:  # white-box: confirm eviction happened
        assert "inst-x" not in github_client.token_cache._tokens


def test_revocation_is_audited_distinctly_from_an_ordinary_permission_denial(
    github_client: GitHubAppClient, mock_github,
):
    server, _ = mock_github
    server.state.revoked_installations.add("inst-revoked")

    with pytest.raises(PermissionDeniedError):
        github_client.get_ref(installation_id="inst-revoked", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")

    events = github_client.audit.events
    revoked_events = [e for e in events if e.outcome == "revoked"]
    assert len(revoked_events) == 1
    assert revoked_events[0].installation_id == "inst-revoked"
    assert revoked_events[0].tenant_id == "tenant-x"
