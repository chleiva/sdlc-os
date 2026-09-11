---
name: sync-deliverable-briefs
description: Regenerate one or more deliverable briefs in docs/deliverables/ after the master spec changes, so a brief never silently drifts from the spec it was extracted from. Use whenever the master spec is revised, or a brief is found to be stale/wrong.
---

# Syncing deliverable briefs to the master spec

`docs/deliverables/*.md` are generated extracts of the master spec, not
a hand-maintained second copy. When the master spec changes, find every
brief that's now stale and regenerate it in full — don't hand-patch a
brief to paper over a spec change; that's exactly the "two systems that
need reconciling" drift the master spec itself warns against.

## 1. Find what's affected

- Open `docs/deliverables/00-README.md` for the file index and
  dependency graph.
- For each brief, its "Master spec pointers" section (and its "quoted
  from master spec §X" callouts) list exactly which sections it
  depends on. A revision touching Section 14.13, for example, is
  relevant to every brief whose pointers list §14.13.
- Don't assume — grep the master spec's diff/changed sections against
  every brief's pointer list rather than guessing which briefs are
  affected from memory.

## 2. Re-read, don't recall

Read the current text of every affected master-spec section directly
before regenerating a brief. A brief is only as good as an accurate
transcription of the spec's *current* state — don't carry forward
wording from a previous revision.

## 3. Regenerate in full

Rewrite the whole brief file (not a partial edit) using the
established template — every existing brief in
`docs/deliverables/` shows the exact shape to match:

- Title + wave + depends-on + blocks
- **Scope** (1–2 paragraphs)
- **Explicitly not in scope** (bullets, pointing to the deliverable
  that does own it)
- **Interfaces you implement** — quote verbatim anything that's a
  pinned cross-deliverable contract (the MCP tool contracts, the Run
  Registry schema/API, the plan artifact, gate-approver rules) since
  those must never paraphrase-drift between the brief and the spec, or
  between two briefs that both quote the same contract
- **Interfaces you consume**
- **Acceptance criteria** (checkboxes)
- **Master spec pointers** (section numbers only — full rationale
  lives in the spec, read on demand, not copied here)

Keep it in the ~35–70 line range the existing briefs establish. A
regenerated brief that balloons well past that is usually a sign the
deliverable's own scope grew in the revision — consider whether it
should split into two deliverables (and update `00-README.md`'s
dependency graph and file index accordingly) rather than just writing a
longer brief.

## 4. Update the index if boundaries changed

If the revision adds, removes, or splits a deliverable (rare — most
revisions just change what an existing deliverable's brief says),
update `00-README.md`'s dependency graph, wave assignment, and file
index to match. Don't let the index and the actual file set drift.

## 5. Verify

- Every brief's verbatim-quoted contract text matches the master
  spec's current wording exactly — a quick diff-by-eye against the
  source section is worth it given these are the pieces every other
  deliverable relies on being byte-identical.
- No brief references a section number that no longer says what the
  brief claims it says.
