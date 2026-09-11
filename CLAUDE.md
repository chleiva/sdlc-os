# SDLC Auto — Repo Guide

This repo currently holds **specification and planning documents only** —
no application code has been written yet. It is the design phase for an
open-source, self-hosted, multi-tenant AI coding-agent platform ("the
System"). Don't assume there's a codebase to build/test here; there
isn't one yet.

## What's here

- `docs/AI Coding Agentic Solution - Specification (Rev 6).md` — the
  **master spec**, currently **Revision 8** (the filename still says
  "Rev 6" — it was never renamed across revisions; the title block
  *inside* the file is the actual revision, always check that, not the
  filename).
- `docs/deliverables/` — the spec decomposed into 16 independent
  build-deliverable briefs, organized into 4 dependency waves (Wave 0
  foundation → Wave 1 core components → Wave 2 gates/hardening → Wave 3
  release/eval). `00-README.md` is the index: dependency graph, ground
  rules for assigning a deliverable to a subagent, file list.

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

## Known quirks

- Not yet a git repository (`git init` hasn't been run). Worth doing
  before real parallel-subagent work starts, so each deliverable can
  work in its own branch/worktree per the spec's own single-threaded-
  ownership principle (§8.1) — applied here to building the System, not
  just inside it.
- The master spec's own filename lags its revision number (see above) —
  don't rename it without checking every reference to the exact
  filename first (`docs/deliverables/00-README.md` names it verbatim).

## Current status

Specification: Revision 8, multi-tenant architecture designed in.
Deliverable briefs: written, none yet assigned or started. No Wave has
begun implementation.
