# source-control — D5: Source-Control Integration (GitHub)

Wave 1 deliverable. Implements F3's source-control MCP contract for real
against a GitHub App installation — never a personal access token: real
RS256 JWT signing and installation-token exchange (short-lived, re-issued
at point of use, never cached past expiry), real `git worktree`
branch/session isolation with `fcntl.flock`-serialized concurrency, and
real HMAC webhook verification wired to evict cached tokens the moment a
revocation event is observed.

A structural fitness test (AST/grep-based) proves no PAT-shaped code path
exists anywhere in this package, not just that the happy path avoids one.

## Install & run tests

```bash
cd services/source-control
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -v
```

74 tests. GitHub itself is mocked (`tests/mock_github_server.py`, stdlib
`http.server`) — no live org/App/repo exists in this environment; git
worktree mechanics are exercised against a real local git repo, no
mocking needed there. See `SETUP.md` for exactly what a human with a real
GitHub org needs to do (App registration, permission/webhook scopes,
installation, private-key handling) before pointing this at a live org.
