"""D10 adversarial pass on D5 (source-control / GitHub App integration).

Extends D5's own already-thorough fitness tests
(`test_no_personal_access_token.py`, `test_revocation.py`) with:

  1. two adversarial PAT-smuggling variants the brief specifically asks
     for (an env var that *looks* like a PAT holder; a credential
     smuggled through an unexpected config field);
  2. confirmation that `generate_app_jwt` rejects non-RSA key material
     (e.g. a PAT-shaped string swapped in for the PEM private key);
  3. the credential-revocation-propagates gap this pass FOUND and FIXED
     (`SourceControlService.handle_installation_webhook`, newly wired to
     `InstallationTokenCache.force_evict`): a verified webhook
     revocation must proactively kill the cached token, not merely wait
     for the next live GitHub call to come back 401.

Drives the REAL `source_control` package throughout, against the real
mock GitHub HTTP server (`source-control/tests/mock_github_server.py`),
imported directly -- the same helper D5's own suite uses.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# `mock_github_server.py` is a test helper (not part of the installed
# `source_control` package) -- import it directly from D5's own tests/
# directory, same pattern job-dispatcher's conftest uses for
# issue-tracker's `mocks/jira_mock_server.py`.
SOURCE_CONTROL_TESTS = Path(__file__).resolve().parent.parent.parent / "source-control" / "tests"
if str(SOURCE_CONTROL_TESTS) not in sys.path:
    sys.path.insert(0, str(SOURCE_CONTROL_TESTS))

from mock_github_server import MockGitHubServer  # noqa: E402

from source_control.app_auth import generate_app_jwt
from source_control.audit import AuditLogger
from source_control.github_client import AppCredentials, GitHubAppClient
from source_control.service import InstallationRegistry, SourceControlService, TenantInstallation
from source_control.webhook import WebhookVerificationError

APP_ID = "918273"
APP_SLUG = "sdlc-auto"
WEBHOOK_SECRET = b"a-real-webhook-secret-0123456789"


@pytest.fixture(scope="module")
def rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return private_key, private_key.public_key(), private_pem


@pytest.fixture
def mock_github(rsa_keypair):
    _, public_key, _ = rsa_keypair
    server = MockGitHubServer(app_id=APP_ID, app_public_key=public_key)
    base_url = server.start()
    try:
        yield server, base_url
    finally:
        server.stop()


def _sign_webhook(payload: bytes, secret: bytes = WEBHOOK_SECRET) -> str:
    return "sha256=" + hmac.new(secret, payload, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------
# 1a. Env var that LOOKS like a PAT holder must have zero effect --
# nothing in source_control reads os.environ for credential material.
# ---------------------------------------------------------------------
def test_pat_shaped_env_var_has_zero_effect_on_outbound_auth(monkeypatch, mock_github, rsa_keypair):
    _, _, private_pem = rsa_keypair
    fake_pat = "ghp_" + "x" * 36
    monkeypatch.setenv("GITHUB_TOKEN", fake_pat)
    monkeypatch.setenv("GH_TOKEN", fake_pat)
    monkeypatch.setenv("GITHUB_PAT", fake_pat)

    server, base_url = mock_github
    server.state.refs[("acme", "app", "main")] = "a" * 40

    creds = AppCredentials(app_id=APP_ID, app_slug=APP_SLUG, private_key_pem=private_pem)
    client = GitHubAppClient(credentials=creds, api_base_url=base_url, audit_logger=AuditLogger())
    # The mock server only accepts a Bearer token it itself issued
    # (`state.issued_tokens`) -- the fake PAT was never issued by it, so
    # if the client had (incorrectly) used the env var as its bearer
    # token this call would 401 as PermissionDeniedError. A successful
    # call is the real, load-bearing proof the env var had zero effect,
    # not just a structural grep.
    client.get_ref(installation_id="inst-1", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")

    # Sanity: the env vars really were set for the duration of the call
    # (i.e. this test would have been a false negative if they weren't).
    for key in ("GITHUB_TOKEN", "GH_TOKEN", "GITHUB_PAT"):
        assert os.environ[key] == fake_pat


# ---------------------------------------------------------------------
# 1b. Credential smuggled through an unexpected config field: both
# TenantInstallation and AppCredentials are frozen dataclasses with a
# fixed field set -- no catch-all kwarg exists to smuggle a token
# through.
# ---------------------------------------------------------------------
def test_tenant_installation_rejects_an_unexpected_credential_field(rsa_keypair, tmp_path):
    _, _, private_pem = rsa_keypair
    with pytest.raises(TypeError):
        TenantInstallation(
            tenant_id="tenant-x",
            installation_id="inst-1",
            app_id=APP_ID,
            app_slug=APP_SLUG,
            private_key_pem=private_pem,
            allowed_repositories=frozenset({"acme/app"}),
            mirror_root=tmp_path,
            access_token="ghp_smuggled_via_unexpected_field",  # type: ignore[call-arg]
        )


def test_app_credentials_rejects_an_unexpected_credential_field(rsa_keypair):
    _, _, private_pem = rsa_keypair
    with pytest.raises(TypeError):
        AppCredentials(
            app_id=APP_ID,
            app_slug=APP_SLUG,
            private_key_pem=private_pem,
            personal_access_token="ghp_smuggled",  # type: ignore[call-arg]
        )


def test_pat_shaped_string_in_place_of_pem_private_key_is_rejected_not_silently_used():
    """A PAT-shaped string smuggled in as if it were the PEM private key
    must fail at JWT-signing time (real RSA-key parsing), never
    silently "work" as a credential of a different shape."""
    fake_pat_as_key = b"ghp_" + b"y" * 36
    with pytest.raises(Exception):
        generate_app_jwt(APP_ID, fake_pat_as_key, now=0)


# ---------------------------------------------------------------------
# 2. Real end-to-end proof: cached token survives across calls to the
#    SAME installation, but two DIFFERENT tenants registered under two
#    DIFFERENT installation_ids never share a cache entry or a client.
# ---------------------------------------------------------------------
def test_two_tenants_never_share_a_cached_client_or_token(mock_github, rsa_keypair, tmp_path):
    _, _, private_pem = rsa_keypair
    server, base_url = mock_github
    server.state.refs[("acme", "repo-a", "main")] = "a" * 40
    server.state.refs[("acme", "repo-b", "main")] = "b" * 40

    registry = InstallationRegistry()
    registry.register(
        TenantInstallation(
            tenant_id="tenant-a", installation_id="inst-a", app_id=APP_ID, app_slug=APP_SLUG,
            private_key_pem=private_pem, allowed_repositories=frozenset({"acme/repo-a"}),
            mirror_root=tmp_path, api_base_url=base_url,
        )
    )
    registry.register(
        TenantInstallation(
            tenant_id="tenant-b", installation_id="inst-b", app_id=APP_ID, app_slug=APP_SLUG,
            private_key_pem=private_pem, allowed_repositories=frozenset({"acme/repo-b"}),
            mirror_root=tmp_path, api_base_url=base_url,
        )
    )
    svc = SourceControlService(registry)

    client_a = svc._client_for(registry.resolve("tenant-a", "acme/repo-a"))
    client_b = svc._client_for(registry.resolve("tenant-b", "acme/repo-b"))
    assert client_a is not client_b
    assert client_a.token_cache is not client_b.token_cache

    # tenant A cannot reach tenant B's repository through its own installation.
    with pytest.raises(Exception):
        registry.resolve("tenant-a", "acme/repo-b")


# ---------------------------------------------------------------------
# 3. The credential-revocation-propagates FIX this pass made: a
#    verified webhook revocation event must proactively evict the
#    cached token/client for that installation_id -- not merely wait
#    for the next live call to fail.
# ---------------------------------------------------------------------
def test_installation_webhook_revocation_proactively_evicts_cached_client(mock_github, rsa_keypair, tmp_path):
    _, _, private_pem = rsa_keypair
    server, base_url = mock_github
    server.state.refs[("acme", "app", "main")] = "a" * 40

    registry = InstallationRegistry()
    registry.register(
        TenantInstallation(
            tenant_id="tenant-x", installation_id="inst-x", app_id=APP_ID, app_slug=APP_SLUG,
            private_key_pem=private_pem, allowed_repositories=frozenset({"acme/app"}),
            mirror_root=tmp_path, api_base_url=base_url,
        )
    )
    svc = SourceControlService(registry)

    # Warm the cache: one real call creates and caches a GitHubAppClient
    # (and, inside it, an installation token) for inst-x.
    installation = registry.resolve("tenant-x", "acme/app")
    client = svc._client_for(installation)
    client.get_ref(installation_id="inst-x", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")
    assert "inst-x" in svc._clients
    assert "inst-x" in client.token_cache._tokens

    # A real GitHub "installation deleted" webhook arrives, correctly
    # HMAC-signed.
    payload = {"action": "deleted", "installation": {"id": "inst-x"}}
    raw_body = json.dumps(payload).encode("utf-8")
    signature = _sign_webhook(raw_body)

    outcome = svc.handle_installation_webhook(
        event_type="installation",
        raw_body=raw_body,
        payload=payload,
        signature_header=signature,
        webhook_secret=WEBHOOK_SECRET,
    )
    assert outcome["outcome"] == "revoked"
    assert outcome["installation_id"] == "inst-x"

    # The cached client (and its cached token) must be gone -- proactively,
    # before any subsequent live call would have surfaced a 401.
    assert "inst-x" not in svc._clients


def test_installation_webhook_with_bad_signature_is_rejected_and_does_not_evict_anything(mock_github, rsa_keypair, tmp_path):
    """Fail-closed: an unsigned/forged webhook must never be able to
    evict another tenant's cache entry (a denial-of-service angle) --
    signature verification must happen before anything else, mirroring
    D1's Sec. 17.3 discipline applied here to GitHub's own webhook."""
    _, _, private_pem = rsa_keypair
    server, base_url = mock_github
    server.state.refs[("acme", "app", "main")] = "a" * 40

    registry = InstallationRegistry()
    registry.register(
        TenantInstallation(
            tenant_id="tenant-x", installation_id="inst-x", app_id=APP_ID, app_slug=APP_SLUG,
            private_key_pem=private_pem, allowed_repositories=frozenset({"acme/app"}),
            mirror_root=tmp_path, api_base_url=base_url,
        )
    )
    svc = SourceControlService(registry)
    installation = registry.resolve("tenant-x", "acme/app")
    client = svc._client_for(installation)
    client.get_ref(installation_id="inst-x", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")
    assert "inst-x" in svc._clients

    payload = {"action": "deleted", "installation": {"id": "inst-x"}}
    raw_body = json.dumps(payload).encode("utf-8")
    forged_signature = "sha256=" + "0" * 64  # not a real HMAC of raw_body

    with pytest.raises(WebhookVerificationError):
        svc.handle_installation_webhook(
            event_type="installation",
            raw_body=raw_body,
            payload=payload,
            signature_header=forged_signature,
            webhook_secret=WEBHOOK_SECRET,
        )
    # Untouched: the forged webhook must not have evicted anything.
    assert "inst-x" in svc._clients


def test_installation_webhook_non_revocation_event_is_ignored_and_does_not_evict(mock_github, rsa_keypair, tmp_path):
    _, _, private_pem = rsa_keypair
    server, base_url = mock_github
    server.state.refs[("acme", "app", "main")] = "a" * 40

    registry = InstallationRegistry()
    registry.register(
        TenantInstallation(
            tenant_id="tenant-x", installation_id="inst-x", app_id=APP_ID, app_slug=APP_SLUG,
            private_key_pem=private_pem, allowed_repositories=frozenset({"acme/app"}),
            mirror_root=tmp_path, api_base_url=base_url,
        )
    )
    svc = SourceControlService(registry)
    installation = registry.resolve("tenant-x", "acme/app")
    client = svc._client_for(installation)
    client.get_ref(installation_id="inst-x", tenant_id="tenant-x", owner="acme", repo="app", ref="heads/main")

    payload = {"action": "created", "installation": {"id": "inst-x"}}  # a NEW install, not a revocation
    raw_body = json.dumps(payload).encode("utf-8")
    signature = _sign_webhook(raw_body)

    outcome = svc.handle_installation_webhook(
        event_type="installation", raw_body=raw_body, payload=payload,
        signature_header=signature, webhook_secret=WEBHOOK_SECRET,
    )
    assert outcome["outcome"] == "ignored"
    assert "inst-x" in svc._clients  # untouched
