"""Real GitHub REST API v3 client, authenticated as a GitHub App
installation (spec Section 15 / Section 17.1) -- never a personal access
token.

Two authentication modes, both real:
  * App-level JWT (see `app_auth.generate_app_jwt`) -- used for exactly
    two endpoints: exchanging itself for an installation token
    (`create_installation_token`), and reading the App's own metadata
    (`get_app_slug`) -- the latter added on a real live run, see its
    own docstring for why.
  * Installation access token (`Authorization: Bearer <token>`) -- used
    for every other call in this module. `app_auth.InstallationTokenCache`
    (owned by this client) is the only place that decides whether a
    cached token is still fresh enough to reuse or must be re-issued.

Uses only the standard library's `urllib` for HTTP -- no third-party HTTP
dependency is needed to speak GitHub's plain REST/JSON API, which keeps
the entire request path auditable in one file.

Request shapes below match GitHub's documented REST API v3 exactly
(paths, verbs, JSON bodies, and the response fields this service actually
reads) so the same code point at `https://api.github.com` in production
and at a local mock server (tests/mock_github_server.py) in tests --
only `api_base_url` changes.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from source_control.app_auth import InstallationToken, InstallationTokenCache, generate_app_jwt
from source_control.audit import AuditLogger
from source_control.errors import (
    AccessRevokedError,
    NotFoundError,
    RateLimitedError,
    SourceControlError,
    UpstreamUnavailableError,
)

_API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class AppCredentials:
    """The App's own identity. `private_key_pem` is the App's private
    key -- per spec Section 17.3, this is expected to arrive already
    unwrapped from this tenant's own KMS-wrapped secret store at the
    point of use, never persisted to disk by this module. `app_slug` is
    GitHub's URL-safe App name, used to derive the bot actor login
    (`audit.bot_actor`)."""

    app_id: str
    app_slug: str
    private_key_pem: bytes


class GitHubAppClient:
    def __init__(
        self,
        *,
        credentials: AppCredentials,
        api_base_url: str = "https://api.github.com",
        audit_logger: AuditLogger | None = None,
        timeout_seconds: float = 15.0,
    ):
        self._creds = credentials
        self._api_base_url = api_base_url.rstrip("/")
        self._audit = audit_logger or AuditLogger()
        self._timeout = timeout_seconds
        self.token_cache = InstallationTokenCache(self)

    # ------------------------------------------------------------------
    # App JWT -> installation token exchange
    # ------------------------------------------------------------------
    def create_installation_token(self, installation_id: str, *, tenant_id: str = "") -> InstallationToken:
        """POST /app/installations/{installation_id}/access_tokens,
        authenticated with the App's own JWT (never an installation
        token, which does not yet exist at this point). Always issues a
        brand-new token -- this method never consults or writes the
        cache; `InstallationTokenCache.get_token` is the caching layer
        above it. `tenant_id` is carried only so a failure here (e.g. a
        revoked installation) is attributed to the right tenant in the
        audit trail -- it plays no role in the request itself."""
        jwt = generate_app_jwt(self._creds.app_id, self._creds.private_key_pem)
        status, headers, body = self._send(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            auth_header=f"Bearer {jwt}",
        )
        if status >= 300:
            raise self._map_http_error(
                status, headers, body,
                context=f"exchanging App JWT for installation token (installation {installation_id})",
                installation_id=installation_id,
                tenant_id=tenant_id, action="create_installation_token", repository=None,
            )
        parsed = json.loads(body)
        import datetime

        expires_at = datetime.datetime.strptime(
            parsed["expires_at"], "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=datetime.timezone.utc).timestamp()
        return InstallationToken(token=parsed["token"], expires_at=expires_at, installation_id=installation_id)

    def get_app_slug(self) -> str:
        """GET /app, authenticated with the App's own JWT (real bug found
        on a real live run: `AppCredentials.app_slug` was being hardcoded
        to `""` by a caller on the theory that it was "only used for
        audit-log actor display" -- but `audit.bot_actor` actually
        *requires* a well-formed, non-empty slug and raises otherwise,
        which surfaced for the first time only once a run reached its
        first real `open_pr` call. GitHub's own `/app` endpoint returns
        the slug the App was registered under -- the one real source of
        truth for it, rather than a human having to transcribe it from
        the App's settings-page URL into an env var by hand). Callable
        with any `AppCredentials` (including one built with `app_slug=""`,
        since this call itself never needs it) -- see `live_run.py` for
        the real bootstrap sequence this enables.

        Deliberately does NOT route failures through `_map_http_error`
        like every other call here: that path always audits via
        `AuditLogger.record`, which itself calls `bot_actor(app_slug)` --
        exactly the call that raises on an empty slug. Since this method
        exists specifically to be callable *before* the real slug is
        known, and it is an app-level bootstrap call with no
        installation/tenant to attribute anyway, a failure here raises a
        plain `UpstreamUnavailableError` with no audit record instead."""
        jwt = generate_app_jwt(self._creds.app_id, self._creds.private_key_pem)
        status, headers, body = self._send("GET", "/app", auth_header=f"Bearer {jwt}")
        if status >= 300:
            try:
                message = json.loads(body).get("message", body) if body else f"HTTP {status}"
            except json.JSONDecodeError:
                message = body or f"HTTP {status}"
            raise UpstreamUnavailableError(f"fetching App metadata (GET /app): {message}")
        return json.loads(body)["slug"]

    # ------------------------------------------------------------------
    # Authenticated REST calls (installation token)
    # ------------------------------------------------------------------
    def _authed_request(
        self,
        method: str,
        path: str,
        *,
        installation_id: str,
        tenant_id: str,
        action: str,
        repository: str | None,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        token = self.token_cache.get_token(installation_id, tenant_id=tenant_id)
        status, headers, resp_body = self._send(
            method, path, auth_header=f"Bearer {token.token}", body=body, query=query,
        )
        if status >= 300:
            error = self._map_http_error(
                status, headers, resp_body,
                context=f"{method} {path}",
                installation_id=installation_id, tenant_id=tenant_id,
                action=action, repository=repository,
            )
            if isinstance(error, AccessRevokedError):
                # A revoked/invalidated installation token must never be
                # handed out again -- drop it from the cache immediately
                # rather than letting a future caller reuse it and get
                # the same failure repeatedly under a misleading "it's
                # just flaky" read.
                self.token_cache.force_evict(installation_id)
            raise error
        self._audit.record(
            app_slug=self._creds.app_slug, installation_id=installation_id, tenant_id=tenant_id,
            action=action, repository=repository, outcome="ok",
        )
        return json.loads(resp_body) if resp_body else {}

    def _send(
        self,
        method: str,
        path: str,
        *,
        auth_header: str,
        body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], str]:
        url = f"{self._api_base_url}{path}"
        if query:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode(query)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", auth_header)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", _API_VERSION)
        req.add_header("User-Agent", f"{self._creds.app_slug}-github-app")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers.items()) if e.headers else {}, e.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise UpstreamUnavailableError(f"upstream unreachable: {e}") from e

    def _map_http_error(
        self, status: int, headers: dict[str, str], body_text: str, *,
        context: str, installation_id: str, tenant_id: str, action: str, repository: str | None,
    ) -> SourceControlError:
        try:
            message = json.loads(body_text).get("message", body_text) if body_text else f"HTTP {status}"
        except json.JSONDecodeError:
            message = body_text or f"HTTP {status}"

        if status == 404:
            self._audit.record(
                app_slug=self._creds.app_slug, installation_id=installation_id, tenant_id=tenant_id,
                action=action, repository=repository, outcome="error", detail=f"not-found: {message}",
            )
            return NotFoundError(f"{context}: {message}")

        remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
        retry_after_hdr = headers.get("Retry-After") or headers.get("retry-after")

        if status == 429 or (status == 403 and remaining == "0"):
            retry_after = int(retry_after_hdr) if retry_after_hdr else 60
            self._audit.record(
                app_slug=self._creds.app_slug, installation_id=installation_id, tenant_id=tenant_id,
                action=action, repository=repository, outcome="error", detail=f"rate-limited: {message}",
            )
            return RateLimitedError(f"{context}: {message}", retry_after_seconds=retry_after)

        if status == 403 and retry_after_hdr is not None and remaining != "0":
            # GitHub's "secondary rate limit" shape: 403 + Retry-After,
            # no X-RateLimit-Remaining involved.
            self._audit.record(
                app_slug=self._creds.app_slug, installation_id=installation_id, tenant_id=tenant_id,
                action=action, repository=repository, outcome="error", detail=f"rate-limited (secondary): {message}",
            )
            return RateLimitedError(f"{context}: {message}", retry_after_seconds=int(retry_after_hdr))

        if status in (401, 403):
            # No rate-limit signal present: this is either a straightforwardly
            # revoked/suspended installation, an expired/invalid token, or a
            # permissions shortfall -- all fold to permission-denied on the
            # wire (spec's four-error taxonomy has no fifth "revoked" code),
            # but are recorded distinctly ("revoked") in the audit trail so a
            # human reviewing NHI access (Section 17.1) can see it as what it
            # is: access ending, not a transient blip.
            self._audit.record(
                app_slug=self._creds.app_slug, installation_id=installation_id, tenant_id=tenant_id,
                action=action, repository=repository, outcome="revoked", detail=message,
            )
            return AccessRevokedError(f"{context}: access denied/revoked ({status}): {message}")

        if 500 <= status < 600:
            self._audit.record(
                app_slug=self._creds.app_slug, installation_id=installation_id, tenant_id=tenant_id,
                action=action, repository=repository, outcome="error", detail=f"upstream-unavailable: {message}",
            )
            return UpstreamUnavailableError(f"{context}: upstream returned {status}: {message}")

        self._audit.record(
            app_slug=self._creds.app_slug, installation_id=installation_id, tenant_id=tenant_id,
            action=action, repository=repository, outcome="error", detail=f"unexpected {status}: {message}",
        )
        return UpstreamUnavailableError(f"{context}: unexpected status {status}: {message}")

    # ------------------------------------------------------------------
    # High-level REST operations (real GitHub API v3 shapes)
    # ------------------------------------------------------------------
    def get_ref(self, *, installation_id: str, tenant_id: str, owner: str, repo: str, ref: str) -> dict[str, Any]:
        """GET /repos/{owner}/{repo}/git/ref/{ref} -- `ref` like
        'heads/main'."""
        return self._authed_request(
            "GET", f"/repos/{owner}/{repo}/git/ref/{ref}",
            installation_id=installation_id, tenant_id=tenant_id,
            action="get_ref", repository=f"{owner}/{repo}",
        )

    def create_ref(
        self, *, installation_id: str, tenant_id: str, owner: str, repo: str, ref: str, sha: str,
    ) -> dict[str, Any]:
        """POST /repos/{owner}/{repo}/git/refs -- `ref` like
        'refs/heads/feature-x'."""
        return self._authed_request(
            "POST", f"/repos/{owner}/{repo}/git/refs",
            installation_id=installation_id, tenant_id=tenant_id,
            action="create_ref", repository=f"{owner}/{repo}",
            body={"ref": ref, "sha": sha},
        )

    def list_pulls(
        self, *, installation_id: str, tenant_id: str, owner: str, repo: str,
        head: str | None = None, base: str | None = None, state: str = "open",
    ) -> list[dict[str, Any]]:
        """GET /repos/{owner}/{repo}/pulls?head=...&base=...&state=..."""
        query = {"state": state}
        if head:
            query["head"] = head
        if base:
            query["base"] = base
        return self._authed_request(
            "GET", f"/repos/{owner}/{repo}/pulls",
            installation_id=installation_id, tenant_id=tenant_id,
            action="list_pulls", repository=f"{owner}/{repo}", query=query,
        )

    def create_pull(
        self, *, installation_id: str, tenant_id: str, owner: str, repo: str,
        head: str, base: str, title: str, body: str, draft: bool = False,
    ) -> dict[str, Any]:
        """POST /repos/{owner}/{repo}/pulls -- opens against the org's
        existing PR flow, never a parallel review mechanism (spec
        Section 15)."""
        return self._authed_request(
            "POST", f"/repos/{owner}/{repo}/pulls",
            installation_id=installation_id, tenant_id=tenant_id,
            action="create_pull", repository=f"{owner}/{repo}",
            body={"head": head, "base": base, "title": title, "body": body, "draft": draft},
        )

    def get_pull(self, *, installation_id: str, tenant_id: str, owner: str, repo: str, number: int) -> dict[str, Any]:
        """GET /repos/{owner}/{repo}/pulls/{number}"""
        return self._authed_request(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}",
            installation_id=installation_id, tenant_id=tenant_id,
            action="get_pull", repository=f"{owner}/{repo}",
        )

    def list_check_runs_for_ref(
        self, *, installation_id: str, tenant_id: str, owner: str, repo: str, ref: str,
    ) -> dict[str, Any]:
        """GET /repos/{owner}/{repo}/commits/{ref}/check-runs -- polls CI
        results without re-implementing test execution (spec Section
        15)."""
        return self._authed_request(
            "GET", f"/repos/{owner}/{repo}/commits/{ref}/check-runs",
            installation_id=installation_id, tenant_id=tenant_id,
            action="list_check_runs_for_ref", repository=f"{owner}/{repo}",
        )

    def get_contents(
        self, *, installation_id: str, tenant_id: str, owner: str, repo: str, path: str, ref: str,
    ) -> dict[str, Any]:
        """GET /repos/{owner}/{repo}/contents/{path}?ref=..."""
        return self._authed_request(
            "GET", f"/repos/{owner}/{repo}/contents/{path}",
            installation_id=installation_id, tenant_id=tenant_id,
            action="get_contents", repository=f"{owner}/{repo}", query={"ref": ref},
        )

    def get_tree(
        self, *, installation_id: str, tenant_id: str, owner: str, repo: str, tree_sha: str, recursive: bool,
    ) -> dict[str, Any]:
        """GET /repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1"""
        query = {"recursive": "1"} if recursive else {}
        return self._authed_request(
            "GET", f"/repos/{owner}/{repo}/git/trees/{tree_sha}",
            installation_id=installation_id, tenant_id=tenant_id,
            action="get_tree", repository=f"{owner}/{repo}", query=query,
        )

    @property
    def audit(self) -> AuditLogger:
        return self._audit
