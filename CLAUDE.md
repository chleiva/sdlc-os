# SDLC Auto — Repo Guide

This repo is building an open-source, self-hosted, multi-tenant AI
coding-agent platform ("the System"). It started as specification/planning
docs only; real implementation is now underway (see Current status) —
don't assume there's nothing to build/test here anymore.

## What's here

- `docs/AI Coding Agentic Solution - Specification (Rev 6).md` — the
  **master spec**, currently **Revision 8** (the filename still says
  "Rev 6" — it was never renamed across revisions; the title block
  *inside* the file is the actual revision, always check that, not the
  filename). Gitignored — internal-only, never pushed to the public repo.
- `docs/deliverables/` — the spec decomposed into 16 independent
  build-deliverable briefs, organized into 4 dependency waves (Wave 0
  foundation → Wave 1 core components → Wave 2 gates/hardening → Wave 3
  release/eval). `00-README.md` is the index: dependency graph, ground
  rules for assigning a deliverable to a subagent, file list. Also
  gitignored, same reason as the master spec.
- `infra/` — F1's OpenTofu/HCL module tree + environments (tracked, public).
- `services/<deliverable>/` — one directory per implemented deliverable
  (`run-registry`, `mcp-stubs`, `index-server`, `issue-tracker`,
  `source-control`, `fleet-dashboard`, `job-dispatcher`, `orchestrator`,
  `tenant-cell`, `verification-pipeline`, ...). Tracked, public. Each is
  Python, has its own `pyproject.toml`/`requirements.txt` + `.venv`, and
  its own README with exact run/test instructions — read the service's
  own README before assuming how to install/run it (a couple require a
  `pip install -e ../<dep> --force-reinstall --no-deps` step after the
  normal install; documented per-service, not universal).

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

## Implementation conventions (established across Wave 0/1)

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
- Every deliverable's own report flags spec ambiguities it had to
  resolve unilaterally and cross-deliverable contract-drift risk (e.g. a
  plan-artifact schema built against §9.5 directly because the
  deliverable that will really emit it hadn't landed yet) — check a new
  deliverable's report for these before treating it as fully reconciled
  with what it depends on.

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

## Current status

Specification: Revision 8, multi-tenant architecture designed in.
Wave 0 (F1 IaC foundation, F2 Run Registry, F3 MCP contracts) — done.
Wave 1 (D1 job dispatcher, D2 orchestrator core, D3 index server, D4
Jira integration, D5 GitHub integration, D6 model serving/tenant cell,
D7 verification pipeline, D8 Fleet dashboard) — done. Wave 2 (D9 gates/
governance, D10 security hardening, D11 observability) and Wave 3 (D12
release engineering, D13 evaluation harness) not yet started.
