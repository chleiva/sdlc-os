"""GitHub App JWT -> installation-token exchange (D5 acceptance criteria:
"Installation tokens expire (~1hr) and are re-issued at point of use, not
cached indefinitely.")"""

from __future__ import annotations

import time

from source_control.app_auth import generate_app_jwt
from source_control.github_client import AppCredentials, GitHubAppClient


def test_app_jwt_is_signed_and_accepted_by_a_real_rs256_verifier(rsa_keypair):
    _, public_key, private_pem = rsa_keypair
    jwt = generate_app_jwt("918273", private_pem)

    from tests.mock_github_server import verify_app_jwt  # exercises real RS256 verification

    verify_app_jwt(jwt, public_key, expected_app_id="918273")  # must not raise


def test_app_jwt_is_rejected_for_wrong_issuer(rsa_keypair):
    _, public_key, private_pem = rsa_keypair
    jwt = generate_app_jwt("918273", private_pem)

    from tests.mock_github_server import JWTRejected, verify_app_jwt

    try:
        verify_app_jwt(jwt, public_key, expected_app_id="some-other-app-id")
        assert False, "expected JWTRejected"
    except JWTRejected:
        pass


def test_app_jwt_signature_is_rejected_under_a_different_key():
    from cryptography.hazmat.primitives.asymmetric import rsa as rsa_mod

    private_a = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    private_b = rsa_mod.generate_private_key(public_exponent=65537, key_size=2048)
    from cryptography.hazmat.primitives import serialization

    pem_a = private_a.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    jwt_signed_by_a = generate_app_jwt("42", pem_a)

    from tests.mock_github_server import JWTRejected, verify_app_jwt

    try:
        verify_app_jwt(jwt_signed_by_a, private_b.public_key(), expected_app_id="42")
        assert False, "a JWT signed by key A must not verify against key B's public key"
    except JWTRejected:
        pass


def test_installation_token_is_fetched_fresh_and_cached_until_near_expiry(github_client: GitHubAppClient, mock_github):
    server, _ = mock_github
    server.state.token_ttl_seconds = 3600  # realistic ~1hr lifetime

    cache = github_client.token_cache
    t1 = cache.get_token("inst-1")
    t2 = cache.get_token("inst-1")  # called again immediately: must reuse, not re-issue
    assert t1.token == t2.token
    assert len([e for e in server.state.issued_tokens]) == 1


def test_installation_token_is_re_issued_once_past_expiry_not_cached_indefinitely(github_client: GitHubAppClient, mock_github):
    """The real, non-comment assertion behind the D5 acceptance criterion:
    once a cached token is within the refresh margin of its stated
    expiry, the very next call re-issues a brand-new token from the
    mock server rather than continuing to hand out the old one."""
    server, _ = mock_github
    server.state.token_ttl_seconds = 1  # force expiry to happen almost immediately

    from source_control.app_auth import InstallationTokenCache

    # A tiny refresh margin so a 1-second TTL still gives us a window to
    # observe "still cached" before "expired, re-issued".
    cache = InstallationTokenCache(github_client, refresh_margin_seconds=0)
    github_client.token_cache = cache  # not required, but keeps this self-contained

    first = cache.get_token("inst-1")
    assert len(server.state.issued_tokens) == 1

    time.sleep(1.2)  # cross the 1-second expiry

    second = cache.get_token("inst-1")
    assert len(server.state.issued_tokens) == 2, "a fresh token must be requested once the cached one has expired"
    assert second.token != first.token, "the re-issued token must not be the same (stale) token as before"
    assert second.expires_at > first.expires_at


def test_installation_token_cache_is_per_installation(github_client: GitHubAppClient, mock_github):
    server, _ = mock_github
    a = github_client.token_cache.get_token("inst-a")
    b = github_client.token_cache.get_token("inst-b")
    assert a.token != b.token
    assert server.state.issued_tokens[a.token] == "inst-a"
    assert server.state.issued_tokens[b.token] == "inst-b"


def test_get_app_slug_fetches_the_real_slug_from_github(github_client: GitHubAppClient, mock_github):
    """Real bug found on this repo's first real live run: a caller
    (deploy/run-worker/live_run.py) hardcoded `AppCredentials.app_slug`
    to `""`, which blew up the very first `audit.bot_actor` call
    (`open_pr`'s bookkeeping) with "does not produce a well-formed GitHub
    bot actor login". Fixed with this real `GET /app` call -- proven here
    against the mock server's real RS256-verified App JWT auth, same as
    `create_installation_token` above."""
    from .conftest import APP_SLUG

    assert github_client.get_app_slug() == APP_SLUG


def test_get_app_slug_works_even_when_this_clients_own_credentials_have_no_slug_yet(mock_github, audit_logger, rsa_keypair):
    """The exact real bootstrap shape `live_run.py` now uses: a
    throwaway client built with `app_slug=""` (since the real slug isn't
    known yet) must still be able to make this one call -- it never
    reads/depends on its own `app_slug`, only the App's private key."""
    from .conftest import APP_ID, APP_SLUG

    _, _, private_pem = rsa_keypair
    _, base_url = mock_github
    bootstrap_client = GitHubAppClient(
        credentials=AppCredentials(app_id=APP_ID, app_slug="", private_key_pem=private_pem),
        api_base_url=base_url,
        audit_logger=audit_logger,
    )
    assert bootstrap_client.get_app_slug() == APP_SLUG


def test_get_app_slug_raises_upstream_unavailable_on_a_real_5xx_with_no_audit_crash(github_client: GitHubAppClient, mock_github):
    """`get_app_slug` must surface a real upstream 5xx as
    `UpstreamUnavailableError` -- and, since it can be called with an
    empty `app_slug` (see above), it must NOT go through the normal
    `_map_http_error` audit path, which would itself raise
    (`bot_actor("")`) and mask the real error."""
    from source_control.errors import UpstreamUnavailableError

    server, _ = mock_github
    server.state.force_5xx_next = 1

    try:
        github_client.get_app_slug()
        assert False, "expected UpstreamUnavailableError"
    except UpstreamUnavailableError:
        pass
