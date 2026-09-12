# SDLC Auto — Repo Guide

This repo implements SDLC Auto, an open-source, self-hosted, multi-tenant
AI coding-agent platform ("the System"). See `README.md` for what the
project is and how it's laid out; this file covers conventions for
anyone — human or AI agent — working in the codebase.

## What's here

- `docs/AI Coding Agentic Solution - Specification (Rev 6).md` — the
  **master spec**, currently **Revision 9** (the filename still says
  "Rev 6" — it was never renamed across revisions; the title block
  *inside* the file is the actual revision, always check that, not the
  filename). Gitignored — internal-only, never pushed to the public repo.
- `docs/deliverables/` — the spec decomposed into 17 independent
  build-deliverable briefs, organized into 5 dependency waves (Wave 0
  foundation → Wave 1 core components → Wave 2 gates/hardening → Wave 3
  release/eval → Wave 4 alternative deployment mode,
  `wave4-D14-docker-compose-deployment.md`). `00-README.md` is the
  index: dependency graph, ground rules for assigning a deliverable to
  a subagent, file list. Also gitignored, same reason as the master spec.
- `infra/` — F1's OpenTofu/HCL module tree + environments (tracked, public).
- `services/<deliverable>/` — one directory per implemented deliverable:
  `run-registry` (F2), `mcp-stubs` (F3), `index-server` (D3),
  `issue-tracker` (D4), `source-control` (D5), `fleet-dashboard` (D8),
  `job-dispatcher` (D1), `orchestrator` (D2), `tenant-cell` (D6),
  `verification-pipeline` (D7), `gates` (D9), `security-hardening`
  (D10), `observability` (D11), `platform-release` (D12),
  `evaluation-harness` (D13), `kms-boundary` (per-tenant KMS envelope
  encryption, spec §17.3 — a real gap D10's audit found, closed as its
  own small service rather than folded into an existing one, consumed
  as a sibling dependency by `source-control`/`issue-tracker`). Tracked,
  public. Each is Python, has its
  own `pyproject.toml`/`requirements.txt` + `.venv`, and its own README
  with exact run/test instructions — read the service's own README
  before assuming how to install/run it (a couple require a `pip
  install -e ../<dep> --force-reinstall --no-deps` step after the
  normal install; documented per-service, not universal).
- `docker-compose.yml` (repo root), `deploy/` (`deploy/caddy`'s reverse-
  proxy config, `deploy/job-dispatcher`'s packaging-only entrypoint/
  config-renderer, `deploy/observability`'s optional Prometheus/Grafana/
  Loki/Tempo profile), and a `Dockerfile` inside each `services/
  <deliverable>/` directory — D14's Docker Compose deployment packaging
  (§14.16, Wave 4). Tracked, public. Packaging only: it doesn't modify
  any service's own logic or entrypoint, only how it's built and
  networked — see `docker-compose.yml`'s own top-of-file comment and
  each `Dockerfile`'s own comment for exactly what's real versus a
  build-time self-check for that service today.

## The one rule everything else follows

**The master spec is the single source of truth.** The deliverable
briefs are *generated extracts* of it, scoped small so a subagent loads
a few dozen lines instead of the whole ~3,900-line document. If a brief
and the master spec ever disagree, **the master spec wins** — fix or
regenerate the brief, never hand-edit a brief to paper over a spec
change, and never let two briefs quietly diverge from each other.

## Working in this repo

- **Revising the master spec** (adding a new numbered revision) follows
  a strict, previously-established convention — use the `revise-spec`
  skill rather than improvising it. It has real gotchas (an ASCII
  callout box with an exact character width, several places that must
  be updated in lockstep).
- **After revising the spec**, check whether any deliverable brief is
  now stale — use the `sync-deliverable-briefs` skill to regenerate the
  affected ones from the current spec, rather than patching them by
  hand.
- Section numbering in the master spec is **additive only**: a new
  subsection is appended at the end of its parent section (e.g. a new
  addition to Section 9 becomes 9.6, not inserted between 9.2 and 9.3),
  so existing cross-references never break.
- New/changed content in the master spec is tagged inline —
  `(New, Rev N)` on a subsection heading, `**(Revised, Rev N)**` inline
  on a changed bullet — so `grep "Rev N"` finds everything a given
  revision touched.
- The master spec is pandoc-derived markdown: apostrophes are escaped
  as `\'`, double quotes as `\"`, em dashes are plain `---`
  (unescaped). Match this in any new prose added to it.

## Implementation conventions

- **Stack**: Python for every service unless a deliverable's own nature
  dictates otherwise (F1/D6's IaC is OpenTofu/HCL). Chosen once, applied
  consistently — don't introduce a second language for a new service
  without a real reason.
- **Real code, mocked external boundary.** Every deliverable that needs
  a live third-party account/cluster/model this environment doesn't have
  (Jira org, GitHub App, cloud/Kubernetes, an LLM endpoint) implements
  the real client/protocol logic for real, and validates it against a
  local mock it builds itself — never fabricated credentials, never a
  skipped integration pretending to be done. Each such deliverable has a
  `SETUP.md` or README section listing exactly what a human with the
  real account/access still needs to do. Follow this pattern for new
  deliverables rather than inventing a different one.
- **One deliverable, one subagent, one directory, non-overlapping
  paths** — the README's single-threaded-ownership rule (§8.1) in
  practice; a deliverable never modifies another's directory, only
  consumes it as a real, editable-installed local dependency (see
  `services/*/pyproject.toml`).
- **Independent verification before anything lands.** A deliverable
  reporting "done" is re-verified from a clean venv (or `tofu
  fmt`/`validate`/`plan`) before being treated as landed — a subagent's
  own passing tests are evidence, not proof, since the venv it tested in
  may not be reproducible from a clean install.
- When a deliverable has to resolve a spec ambiguity unilaterally, or
  build against another deliverable's not-yet-landed contract (e.g. a
  plan-artifact schema built directly from §9.5's spec text because the
  component that actually emits it hadn't been built yet), it says so
  explicitly in its own README/docstrings, flagged for reconciliation
  once the real dependency exists. Check for these before assuming two
  interdependent components are fully in sync.

## Known quirks

- The master spec's own filename lags its revision number (see above) —
  don't rename it without checking every reference to the exact
  filename first (`docs/deliverables/00-README.md` names it verbatim).
- A Stop hook (personal, `.claude/settings.local.json`, gitignored) auto-
  commits and pushes every changed file straight to `main` at the end of
  a turn — no per-commit review gate. Deliberate, narrow choice for this
  meta-repo's own development; not a pattern the System itself follows
  (the spec's own §12 requires human approval before anything merges to
  a protected branch — that rule governs the System being built here,
  not how this repo builds it).

## Known cross-deliverable gaps (read before real deployment)

Recurring findings across multiple deliverables' own reports, worth a
human decision before this goes anywhere near real tenant data:

- **F2's Run Registry schema has no column for**: a plan artifact, fine-
  grained subtask progress, checkpoint *count* (only the latest
  pointer), per-run stage budget, an audit-event table, or a
  `platform_version` field. Several deliverables (`orchestrator`,
  `gates`, `platform-release`) worked around this with their own durable
  JSON/JSONL stores rather than changing F2's schema unilaterally — that
  was the right call for each in isolation, but it's worth deciding
  whether some of this should become first-class Registry schema instead
  of five parallel ad-hoc stores.
- **No KMS/secret-decryption boundary exists in code anywhere** (spec
  §17.3) — D10's audit went looking for it in D5/D6 and confirmed the
  gap is real, not just unaudited.
- **`orchestrator`'s model-call and sandbox-hardware boundaries are
  real, tested interfaces with mocked/subprocess-based backends** — by
  necessity, since no live LLM endpoint or microVM runtime exists in any
  build environment so far. Wiring a real model endpoint (via D6) and a
  real Firecracker/Kata/gVisor backend are the two concrete integration
  points still open before this is a working agent, not just a well-
  tested state machine around one.
- **`job-dispatcher`'s same-tenant request coalescing is in-memory-only**
  — correct for one replica, not yet safe for the multi-replica
  deployment §14.4 calls for (needs a shared store — Redis, or routed
  through the Registry).
- **`verification-pipeline`'s plan-artifact schema was built directly
  from spec §9.5 text**, since D7 landed before `orchestrator` did — it
  hasn't been diffed against what `orchestrator` actually emits.
  Reconcile before relying on both together.
- **`orchestrator` has no real process entrypoint** — it's a library
  driven by tests so far, not something any other component in this
  codebase calls at runtime (`job-dispatcher` doesn't dispatch to it).
  D14's Docker Compose packaging works around this honestly: its
  container builds the image and runs the package's own test suite as
  a build-time self-check, then exits, rather than staying up as a
  service. A real entrypoint `job-dispatcher` can actually call is
  still open.
- **The Run Registry has no network-reachable server** — every
  consumer (`job-dispatcher`, `fleet-dashboard`, `orchestrator`,
  `gates`) links F2 in as an embedded library against one shared SQLite
  file, never a client/server call. D14's Docker Compose packaging
  works around this with a shared Docker volume, not a real Registry
  service — the same underlying gap the first bullet above already
  flags, now also visible in how the compose stack has to be wired.

## Current status

Specification: Revision 9, multi-tenant architecture designed in. All 17
deliverables across all 5 waves — Wave 0 (F1, F2, F3), Wave 1 (D1–D8),
Wave 2 (D9–D11), Wave 3 (D12, D13), Wave 4 (D14) — have a real,
independently-verified implementation under `services/`/`infra/` (D14
additionally under the repo root: `docker-compose.yml`, `deploy/`, and a
`Dockerfile` per service). Two deployment modes now exist side by side:
the original multi-tenant, self-hosted-GPU cloud architecture, and a
single-tenant Docker Compose stack that delegates model inference to an
external API-key vendor (§13.8) — see the top-level README's "Quick
start" and "Multi-tenant cloud deployment" sections. Two gaps from the
Docker Compose pass are worth stating plainly rather than glossing over:
there is no real orchestrator process entrypoint yet (it's a library
driven by tests so far — its container currently just runs its test
suite as a self-check, not a running service), and the Run Registry has
no network-reachable server (every consumer uses it as an embedded
library against one SQLite file — the compose packaging works around
this with a shared Docker volume). See "Known cross-deliverable gaps"
above for the full list of what's still open before a real deployment;
see each service's own README for what's real versus mocked at its own
external boundary.
