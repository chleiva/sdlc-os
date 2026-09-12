# Contributing to SDLC Auto

Thanks for considering a contribution. This file is the entry point for
*how* to contribute; `CLAUDE.md` (in the repo root) is the detailed repo
guide — read that too, especially before touching the master spec or
adding a new deliverable, since it has real conventions with sharp edges
(exact section-numbering rules, an install-order quirk most services
share, the single-threaded-ownership rule per directory).

## Code of Conduct

Participation in this project is governed by our
[Code of Conduct](CODE_OF_CONDUCT.md).

## Ground rules

- **Real code, mocked external boundary.** Every deliverable that needs
  a live third-party account/cluster/model this environment doesn't have
  implements the real client/protocol logic for real, validated against
  a local mock it builds itself — never a fabricated credential, never a
  skipped integration pretending to be done. See any `services/*`
  README for the pattern.
- **One deliverable, one directory, non-overlapping paths.** A change
  to `services/<name>` shouldn't need to touch another service's
  directory — consume it as a real, editable-installed local dependency
  instead (see that service's own `pyproject.toml`).
- **Independent verification before landing.** Run the affected
  service's test suite from a clean venv (its own README has the exact
  install sequence — several need a specific sibling-install order,
  documented per-service) before opening a PR, and again after your
  change. A passing suite in a venv you've been reusing all day is
  evidence, not proof.
- **Never commit secrets.** `.env`, `deploy/secrets/`, and any real
  API key/private key/token never belong in a commit — `.gitignore`
  already excludes the known local-secret paths; if you add a new kind
  of local secret file, add it there too, in the same PR.

## Making a change

1. Fork the repo and create a branch off `main`.
2. Find the right `services/<name>` (or `infra/`) directory — check its
   README for the exact install/test sequence first, it usually differs
   slightly from a plain `pip install -e .`.
3. Make your change, add/update tests in the same directory.
4. Run that service's test suite (and any other service's suite whose
   shared dependency you touched — its README says which).
5. Open a PR. CI (`.github/workflows/ci.yml`) runs every service's own
   test suite, `docker compose config`, and an OpenTofu format check —
   all must pass.

## Reporting bugs / proposing features

Open a GitHub issue using the provided templates. For a suspected
security vulnerability, see [SECURITY.md](SECURITY.md) instead — please
don't open a public issue for those.

## Revising the master specification

The master spec (`docs/AI Coding Agentic Solution - Specification
(Rev N).md`) and the per-deliverable briefs under `docs/deliverables/`
are internal-only and gitignored — they aren't part of this public
checkout. If you're a maintainer working from a checkout that has them,
`CLAUDE.md` documents the exact revision/brief-sync conventions.
