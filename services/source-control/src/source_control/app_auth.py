"""GitHub App authentication: JWT-to-installation-token exchange.

Real GitHub App auth (per master spec Section 15 / Section 17.1), no
personal access token anywhere in this module or anything it calls:

  1. The App authenticates itself to GitHub as *the App* (not an
     installation) with a JWT signed by the App's own RSA private key
     (RS256), carrying `iss` (the App ID), `iat`, and `exp` -- GitHub
     caps `exp` at 10 minutes from `iat`; we use 9 to leave margin.
  2. That JWT is exchanged, at the point of use, for a short-lived
     (~1 hour) *installation* access token scoped to one installation
     (POST /app/installations/{id}/access_tokens). This is the token
     every subsequent GitHub REST call authenticates with.

`InstallationTokenCache.get_token` is the only place a caller obtains an
installation token, and it is the acceptance-criterion-bearing piece:
tokens are cached only up to their own `expires_at` (minus a safety
margin), never indefinitely, and a new token is fetched fresh at point of
use once the cached one is at/past that margin -- see
tests/test_app_auth.py for a real (not commented) assertion of this.

JWT signing uses `cryptography` directly (RS256 = RSASSA-PKCS1-v1_5 over
SHA-256) rather than pulling in a third JWT library, so the entire signing
path is auditable in ~30 lines here.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

if TYPE_CHECKING:
    from source_control.github_client import GitHubAppClient

# GitHub caps the App JWT lifetime at 10 minutes; we ask for less to
# leave a safety margin against clock skew between us and GitHub.
_JWT_LIFETIME_SECONDS = 9 * 60
# Allow for up to 60s of clock drift versus GitHub's clock (GitHub's own
# recommendation), by backdating `iat`.
_CLOCK_DRIFT_ALLOWANCE_SECONDS = 60

# Refresh an installation token this many seconds before its stated
# expiry, so a request never starts with a token that could expire
# mid-flight.
_TOKEN_REFRESH_MARGIN_SECONDS = 30


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_app_jwt(app_id: str, private_key_pem: bytes, *, now: float | None = None) -> str:
    """Build and sign a GitHub App JWT (RS256) per GitHub's documented
    shape: header {"alg": "RS256", "typ": "JWT"}, payload {"iat", "exp",
    "iss"}. `now` is injectable for tests; defaults to wall-clock time.
    """
    now = time.time() if now is None else now
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(private_key, RSAPrivateKey):
        raise ValueError("GitHub App private key must be an RSA key (RS256)")

    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iat": int(now) - _CLOCK_DRIFT_ALLOWANCE_SECONDS,
        "exp": int(now) + _JWT_LIFETIME_SECONDS,
        "iss": str(app_id),
    }
    signing_input = f"{_b64url(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{_b64url(json.dumps(payload, separators=(',', ':')).encode())}"
    signature = private_key.sign(
        signing_input.encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return f"{signing_input}.{_b64url(signature)}"


@dataclass(frozen=True)
class InstallationToken:
    token: str
    expires_at: float  # unix timestamp
    installation_id: str

    def is_expired(self, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        return now >= self.expires_at


class InstallationTokenCache:
    """Caches at most one installation token per installation_id, honoring
    its real expiry -- never used past `expires_at - margin`, and never
    reused across a natural expiry. This is the concrete mechanism behind
    the F3/D5 acceptance criterion "installation tokens expire (~1hr) and
    are re-issued at point of use, not cached indefinitely."

    Thread-safe: multiple concurrent callers for the same installation
    never fetch two tokens where one would do (a lock is held around the
    check-then-fetch), and never hand out a token past its margin.
    """

    def __init__(self, client: "GitHubAppClient", *, refresh_margin_seconds: int = _TOKEN_REFRESH_MARGIN_SECONDS):
        self._client = client
        self._refresh_margin = refresh_margin_seconds
        self._tokens: dict[str, InstallationToken] = {}
        self._lock = threading.Lock()

    def get_token(self, installation_id: str, *, tenant_id: str = "", now: float | None = None) -> InstallationToken:
        now = time.time() if now is None else now
        with self._lock:
            cached = self._tokens.get(installation_id)
            if cached is not None and now < (cached.expires_at - self._refresh_margin):
                return cached
            fresh = self._client.create_installation_token(installation_id, tenant_id=tenant_id)
            self._tokens[installation_id] = fresh
            return fresh

    def force_evict(self, installation_id: str) -> None:
        """Used when a call comes back 401/403 mid-lifetime (e.g. the
        installation was revoked or the token was otherwise invalidated
        server-side) -- drop any cached copy so nothing hands it out
        again, without turning that into a retry loop."""
        with self._lock:
            self._tokens.pop(installation_id, None)
