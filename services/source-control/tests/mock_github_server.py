"""A local HTTP server that mimics GitHub's real REST API v3 response
shapes closely enough to exercise `source_control.github_client` and
`source_control.app_auth` end-to-end, with no network access and no real
GitHub account -- per the D5 brief's "validate it against a local mock
HTTP server you build that mimics GitHub's actual API responses."

This is NOT a fake/simplified protocol: it speaks the same paths, verbs,
JSON bodies, status codes, and headers (X-RateLimit-Remaining, Retry-After)
that api.github.com itself uses for the endpoints this service calls, and
it cryptographically verifies the incoming App JWT's RS256 signature
against the App's real public key -- a wrong or expired JWT is rejected
here exactly as GitHub would reject it.
"""

from __future__ import annotations

import base64
import json
import re
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.exceptions import InvalidSignature


def _b64url_decode(segment: str) -> bytes:
    padding_needed = -len(segment) % 4
    return base64.urlsafe_b64decode(segment + "=" * padding_needed)


class JWTRejected(Exception):
    pass


def verify_app_jwt(token: str, public_key: RSAPublicKey, *, expected_app_id: str, now: float | None = None) -> None:
    now = time.time() if now is None else now
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTRejected("malformed JWT")
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    signature = _b64url_decode(parts[2])
    try:
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise JWTRejected("bad signature") from e

    payload = json.loads(_b64url_decode(parts[1]))
    if payload.get("iss") != expected_app_id:
        raise JWTRejected(f"unexpected iss claim: {payload.get('iss')!r}")
    if payload.get("exp", 0) < now:
        raise JWTRejected("expired JWT")
    if payload.get("iat", 0) > now + 60:
        raise JWTRejected("iat too far in the future")


@dataclass
class MockGitHubState:
    app_id: str
    app_public_key: RSAPublicKey
    app_slug: str = "sdlc-auto-mock"
    revoked_installations: set = field(default_factory=set)
    force_5xx_next: int = 0
    force_rate_limit_next: int = 0
    rate_limit_retry_after: int = 1
    token_ttl_seconds: int = 3600
    issued_tokens: dict = field(default_factory=dict)  # token -> installation_id
    _token_seq: int = 0

    pulls: dict = field(default_factory=dict)  # (owner, repo) -> list[dict]
    next_pr_number: dict = field(default_factory=dict)  # (owner, repo) -> int
    check_runs: dict = field(default_factory=dict)  # (owner, repo, sha) -> list[dict]
    refs: dict = field(default_factory=dict)  # (owner, repo, branch) -> sha
    contents: dict = field(default_factory=dict)  # (owner, repo, branch, path) -> dict
    trees: dict = field(default_factory=dict)  # (owner, repo, sha) -> list[dict]

    call_log: list = field(default_factory=list)  # list[(method, path)]

    def issue_token(self, installation_id: str) -> tuple[str, float]:
        self._token_seq += 1
        token = f"ghs_mock{self._token_seq:08d}"
        expires_at = time.time() + self.token_ttl_seconds
        self.issued_tokens[token] = installation_id
        return token, expires_at

    def add_pr(self, owner: str, repo: str, *, head: str, base: str, title: str, body: str, draft: bool, head_sha: str) -> dict:
        key = (owner, repo)
        number = self.next_pr_number.get(key, 1)
        self.next_pr_number[key] = number + 1
        pr = {
            "number": number,
            "html_url": f"https://github.com/{owner}/{repo}/pull/{number}",
            "state": "open",
            "draft": draft,
            "merged": False,
            "mergeable": True,
            "title": title,
            "body": body,
            "head": {"ref": head, "sha": head_sha},
            "base": {"ref": base},
        }
        self.pulls.setdefault(key, []).append(pr)
        return pr


class MockGitHubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - silence default stderr logging
        pass

    # -- helpers ----------------------------------------------------
    @property
    def state(self) -> MockGitHubState:
        return self.server.state  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict | list, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _bearer_token(self) -> str | None:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        return auth[len("Bearer "):]

    def _path_and_query(self):
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(self.path)
        return parsed.path, {k: v[0] for k, v in parse_qs(parsed.query).items()}

    def _maybe_inject_failure(self) -> bool:
        st = self.state
        if st.force_5xx_next > 0:
            st.force_5xx_next -= 1
            self._json(503, {"message": "Service Unavailable (mock upstream outage)"})
            return True
        if st.force_rate_limit_next > 0:
            st.force_rate_limit_next -= 1
            self._json(
                403,
                {"message": "API rate limit exceeded for installation."},
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + st.rate_limit_retry_after),
                    "Retry-After": str(st.rate_limit_retry_after),
                },
            )
            return True
        return False

    def _authenticate_installation(self) -> str | None:
        """Returns the installation_id for a valid, non-revoked bearer
        installation token, or None after already writing a 401
        response."""
        token = self._bearer_token()
        st = self.state
        if token is None or token not in st.issued_tokens:
            self._json(401, {"message": "Bad credentials"})
            return None
        installation_id = st.issued_tokens[token]
        if installation_id in st.revoked_installations:
            self._json(401, {"message": "Bad credentials"})
            return None
        return installation_id

    # -- routing ------------------------------------------------------
    def do_POST(self):
        path, _ = self._path_and_query()
        self.state.call_log.append(("POST", path))

        m = re.fullmatch(r"/app/installations/(?P<iid>[^/]+)/access_tokens", path)
        if m:
            return self._handle_create_installation_token(m.group("iid"))

        if self._maybe_inject_failure():
            return

        m = re.fullmatch(r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/git/refs", path)
        if m:
            installation_id = self._authenticate_installation()
            if installation_id is None:
                return
            body = self._read_body()
            ref = body["ref"]
            sha = body["sha"]
            branch = ref.removeprefix("refs/heads/")
            self.state.refs[(m.group("owner"), m.group("repo"), branch)] = sha
            return self._json(201, {"ref": ref, "object": {"sha": sha, "type": "commit"}})

        m = re.fullmatch(r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pulls", path)
        if m:
            installation_id = self._authenticate_installation()
            if installation_id is None:
                return
            body = self._read_body()
            owner, repo = m.group("owner"), m.group("repo")
            head_branch = body["head"]
            base_branch = body["base"]
            head_sha = self.state.refs.get((owner, repo, head_branch), "0" * 40)
            pr = self.state.add_pr(
                owner, repo, head=head_branch, base=base_branch,
                title=body["title"], body=body.get("body", ""),
                draft=bool(body.get("draft", False)), head_sha=head_sha,
            )
            return self._json(201, pr)

        self._json(404, {"message": "Not Found"})

    def do_GET(self):
        path, query = self._path_and_query()
        self.state.call_log.append(("GET", path))

        if path == "/app":
            return self._handle_get_app()

        if self._maybe_inject_failure():
            return

        m = re.fullmatch(r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/git/ref/heads/(?P<branch>.+)", path)
        if m:
            installation_id = self._authenticate_installation()
            if installation_id is None:
                return
            key = (m.group("owner"), m.group("repo"), m.group("branch"))
            sha = self.state.refs.get(key)
            if sha is None:
                return self._json(404, {"message": "Not Found"})
            return self._json(200, {"ref": f"refs/heads/{m.group('branch')}", "object": {"sha": sha, "type": "commit"}})

        m = re.fullmatch(r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pulls", path)
        if m:
            installation_id = self._authenticate_installation()
            if installation_id is None:
                return
            owner, repo = m.group("owner"), m.group("repo")
            prs = self.state.pulls.get((owner, repo), [])
            head_filter = query.get("head")
            base_filter = query.get("base")
            state_filter = query.get("state", "open")
            results = []
            for pr in prs:
                if state_filter != "all" and pr["state"] != state_filter:
                    continue
                if head_filter and f"{owner}:{pr['head']['ref']}" != head_filter:
                    continue
                if base_filter and pr["base"]["ref"] != base_filter:
                    continue
                results.append(pr)
            return self._json(200, results)

        m = re.fullmatch(r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pulls/(?P<number>\d+)", path)
        if m:
            installation_id = self._authenticate_installation()
            if installation_id is None:
                return
            owner, repo, number = m.group("owner"), m.group("repo"), int(m.group("number"))
            for pr in self.state.pulls.get((owner, repo), []):
                if pr["number"] == number:
                    return self._json(200, pr)
            return self._json(404, {"message": "Not Found"})

        m = re.fullmatch(r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/commits/(?P<sha>[^/]+)/check-runs", path)
        if m:
            installation_id = self._authenticate_installation()
            if installation_id is None:
                return
            key = (m.group("owner"), m.group("repo"), m.group("sha"))
            runs = self.state.check_runs.get(key, [])
            return self._json(200, {"total_count": len(runs), "check_runs": runs})

        m = re.fullmatch(r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/contents/(?P<path>.+)", path)
        if m:
            installation_id = self._authenticate_installation()
            if installation_id is None:
                return
            branch = query.get("ref", "main")
            key = (m.group("owner"), m.group("repo"), branch, m.group("path"))
            entry = self.state.contents.get(key)
            if entry is None:
                return self._json(404, {"message": "Not Found"})
            return self._json(200, entry)

        m = re.fullmatch(r"/repos/(?P<owner>[^/]+)/(?P<repo>[^/]+)/git/trees/(?P<sha>[^/]+)", path)
        if m:
            installation_id = self._authenticate_installation()
            if installation_id is None:
                return
            key = (m.group("owner"), m.group("repo"), m.group("sha"))
            tree = self.state.trees.get(key)
            if tree is None:
                return self._json(404, {"message": "Not Found"})
            return self._json(200, {"sha": m.group("sha"), "tree": tree, "truncated": False})

        self._json(404, {"message": "Not Found"})

    def _handle_get_app(self) -> None:
        """GET /app -- real GitHub App-level JWT auth (no installation
        token involved), returns the App's own metadata. Added to
        exercise `github_client.GitHubAppClient.get_app_slug`, itself
        added after a real live run found `audit.bot_actor` raising on
        an empty `app_slug` (see that method's docstring)."""
        st = self.state
        jwt = self._bearer_token()
        if jwt is None:
            return self._json(401, {"message": "A JSON web token could not be decoded"})
        try:
            verify_app_jwt(jwt, st.app_public_key, expected_app_id=st.app_id)
        except JWTRejected as e:
            return self._json(401, {"message": f"Bad credentials: {e}"})

        if self._maybe_inject_failure():
            return

        self._json(200, {"id": int(st.app_id) if st.app_id.isdigit() else 1, "slug": st.app_slug, "name": st.app_slug})

    def _handle_create_installation_token(self, installation_id: str) -> None:
        st = self.state
        jwt = self._bearer_token()
        if jwt is None:
            return self._json(401, {"message": "A JSON web token could not be decoded"})
        try:
            verify_app_jwt(jwt, st.app_public_key, expected_app_id=st.app_id)
        except JWTRejected as e:
            return self._json(401, {"message": f"Bad credentials: {e}"})

        if self._maybe_inject_failure():
            return

        if installation_id in st.revoked_installations:
            # Real GitHub behavior for an uninstalled/suspended
            # installation: the App-level JWT is fine (the App itself
            # still exists), but this specific installation no longer
            # grants anything.
            return self._json(401, {"message": "Bad credentials"})

        token, expires_at = st.issue_token(installation_id)
        import datetime

        expires_at_iso = datetime.datetime.fromtimestamp(expires_at, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._json(201, {
            "token": token,
            "expires_at": expires_at_iso,
            "permissions": {"contents": "write", "pull_requests": "write"},
            "repository_selection": "selected",
        })


class MockGitHubServer:
    def __init__(self, *, app_id: str, app_public_key: RSAPublicKey, app_slug: str = "sdlc-auto-mock"):
        self.state = MockGitHubState(app_id=app_id, app_public_key=app_public_key, app_slug=app_slug)
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), MockGitHubHandler)
        self._httpd.state = self.state  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    def start(self) -> str:
        self._thread.start()
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
