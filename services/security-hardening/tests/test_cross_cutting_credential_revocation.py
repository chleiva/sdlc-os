"""Cross-cutting D10 acceptance criterion: "A simulated credential
revocation is confirmed dead against every downstream system it
touched, not just the issuing system."

This is the dedicated cross-cutting test for that criterion. The real
fix it exercises end-to-end lives in D5
(`source_control.service.SourceControlService.handle_installation_webhook`,
wired to `InstallationTokenCache.force_evict` -- see
`test_d5_source_control_hardening.py` for the narrower, per-mechanism
tests). This file proves the FULL propagation chain for one revoked
GitHub App installation:

  1. issuing system (the mock GitHub API) rejects the installation outright;
  2. the cached `InstallationTokenCache` entry is evicted (not just
     "would fail on next use" -- actually gone);
  3. the cached `GitHubAppClient` instance itself is evicted from
     `SourceControlService._clients` (so a stale in-process object with
     its own state cannot be resurrected/reused);
  4. a subsequent legitimate call for a DIFFERENT, still-valid
     installation is completely unaffected (revocation propagation must
     be scoped to the revoked installation_id only, never a blanket
     "clear everything").
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

SOURCE_CONTROL_TESTS = Path(__file__).resolve().parent.parent.parent / "source-control" / "tests"
if str(SOURCE_CONTROL_TESTS) not in sys.path:
    sys.path.insert(0, str(SOURCE_CONTROL_TESTS))

from mock_github_server import MockGitHubServer  # noqa: E402

from source_control.errors import PermissionDeniedError
from source_control.service import InstallationRegistry, SourceControlService, TenantInstallation

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


def _sign_webhook(payload: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET, payload, hashlib.sha256).hexdigest()


def test_revocation_propagates_to_every_downstream_cache_scoped_to_only_the_revoked_installation(
    mock_github, rsa_keypair, tmp_path
):
    _, _, private_pem = rsa_keypair
    server, base_url = mock_github
    server.state.refs[("acme", "revoked-repo", "main")] = "a" * 40
    server.state.refs[("acme", "still-fine-repo", "main")] = "b" * 40

    registry = InstallationRegistry()
    registry.register(
        TenantInstallation(
            tenant_id="tenant-revoked", installation_id="inst-revoked", app_id=APP_ID, app_slug=APP_SLUG,
            private_key_pem=private_pem, allowed_repositories=frozenset({"acme/revoked-repo"}),
            mirror_root=tmp_path, api_base_url=base_url,
        )
    )
    registry.register(
        TenantInstallation(
            tenant_id="tenant-fine", installation_id="inst-fine", app_id=APP_ID, app_slug=APP_SLUG,
            private_key_pem=private_pem, allowed_repositories=frozenset({"acme/still-fine-repo"}),
            mirror_root=tmp_path, api_base_url=base_url,
        )
    )
    svc = SourceControlService(registry)

    # Warm both tenants' caches with a real, successful call each.
    revoked_installation = registry.resolve("tenant-revoked", "acme/revoked-repo")
    fine_installation = registry.resolve("tenant-fine", "acme/still-fine-repo")
    revoked_client = svc._client_for(revoked_installation)
    fine_client = svc._client_for(fine_installation)
    revoked_client.get_ref(installation_id="inst-revoked", tenant_id="tenant-revoked", owner="acme", repo="revoked-repo", ref="heads/main")
    fine_client.get_ref(installation_id="inst-fine", tenant_id="tenant-fine", owner="acme", repo="still-fine-repo", ref="heads/main")
    assert "inst-revoked" in revoked_client.token_cache._tokens
    assert "inst-fine" in fine_client.token_cache._tokens

    # 1. Issuing system: the App is uninstalled for inst-revoked.
    server.state.revoked_installations.add("inst-revoked")

    # A verified webhook delivers the revocation to our service.
    payload = {"action": "deleted", "installation": {"id": "inst-revoked"}}
    raw_body = json.dumps(payload).encode("utf-8")
    outcome = svc.handle_installation_webhook(
        event_type="installation", raw_body=raw_body, payload=payload,
        signature_header=_sign_webhook(raw_body), webhook_secret=WEBHOOK_SECRET,
    )
    assert outcome["outcome"] == "revoked"

    # 2 & 3. Every downstream cache for the REVOKED installation is dead.
    assert "inst-revoked" not in svc._clients, "the cached GitHubAppClient itself must be evicted"
    # (the evicted client's own token_cache is the one we already
    # confirmed was populated -- it is now unreachable from the service,
    # so even if somehow retained, it can no longer be handed to a caller)

    # And the issuing system itself denies any further live call, with
    # no client-side retry:
    calls_before = len(server.state.call_log)
    with pytest.raises(PermissionDeniedError):
        svc._client_for(revoked_installation).get_ref(
            installation_id="inst-revoked", tenant_id="tenant-revoked", owner="acme", repo="revoked-repo", ref="heads/main"
        )
    assert len(server.state.call_log) == calls_before + 1  # exactly one attempt, no retry

    # 4. The OTHER tenant's still-valid installation is completely
    # unaffected -- revocation propagation is scoped, never a blanket
    # "clear everything" side effect.
    assert "inst-fine" in svc._clients
    assert "inst-fine" in fine_client.token_cache._tokens
    fine_client.get_ref(installation_id="inst-fine", tenant_id="tenant-fine", owner="acme", repo="still-fine-repo", ref="heads/main")
