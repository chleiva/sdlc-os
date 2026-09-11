---
name: revise-spec
description: Add a new numbered revision to the AI Coding Agentic Solution specification (docs/AI Coding Agentic Solution - Specification (Rev 6).md) — bump the revision header, extend the WHAT CHANGED callout box at its exact character width, add the Section 1 narrative paragraph, and tag new/changed content per section. Use whenever the master spec needs a new revision.
---

# Revising the master spec

The master spec has an established revision convention (currently at
Revision 8). Follow it exactly — five separate places need to move
together, and the callout box has a hard formatting constraint that
silently breaks if skipped.

## Before you start

1. Read the current title block (top ~40 lines) and the current
   "WHAT CHANGED" box to see the last revision number and what it
   already covers.
2. Know what you're adding *before* editing: which sections are
   brand-new (`(New, Rev N)`) vs. substantively changed
   (`(Revised, Rev N)`). A cosmetic tweak doesn't need a revision at
   all — only substantive content changes do.

## The five things to update, in order

1. **Title block**: bump `**Revision N ---`, and prepend the new
   revision to the `*Supersedes ...*` line (keep the full history —
   look at the current line for the exact grouping-by-date pattern
   already in use).

2. **The WHAT CHANGED callout box**: bump the header
   (`REVISION 3 → N`), then append one new paragraph inside the box
   summarizing the revision. **This box is a pandoc grid table with a
   fixed width — every content line must be exactly 73 characters**
   (`"| " + text.ljust(69) + " |"`, interior text width 69 chars).
   Don't hand-wrap this — use a script:

   ```python
   import textwrap
   text = "Revision N does X, Y, and Z (Section A.B, ...)."
   for line in textwrap.wrap(text, width=69):
       formatted = "| " + line.ljust(69) + " |"
       assert len(formatted) == 73
       print(formatted)
   ```

   Verify every generated line is exactly 73 characters before pasting
   it in — a mismatched width visibly breaks the box's right border.

3. **Section 1's narrative**: add a paragraph (matching the style of
   the existing per-revision paragraphs there) describing what the new
   revision adds and why, placed right before the
   "Sections carried forward from earlier revisions..." paragraph.

4. **Per-section tags**: every brand-new subsection gets
   `(New, Rev N)` appended to its heading. Every substantively changed
   existing bullet/paragraph gets an inline `**(Revised, Rev N)**`
   marker. Never retag older revisions' content.

5. **Section numbering is additive only**: a new subsection is
   appended at the *end* of its parent section (check the highest
   existing subsection number first — e.g. if Section 9 currently ends
   at 9.5, a new one is 9.6), never inserted mid-sequence. This keeps
   every existing cross-reference (`Section 9.3`, etc.) stable across
   revisions.

## Other things a revision often touches

- **Glossary** (near the end, before "References"): add an entry for
  any newly-named concept, tagged `(New, Rev N)`.
- **Risk register** (Section 22): if the revision introduces a new
  failure mode, add a row. It's a pandoc simple table with fixed column
  widths (28 / 29 / 29 chars) — generate new rows with a small
  `textwrap`-per-column script rather than hand-aligning them; the
  existing rows show the exact format to match.
- **Phased rollout** (Section 21): if the revision changes what counts
  as "delivered," consider whether a new phase or phase-gate criterion
  is needed.
- **Cross-references**: if the revision changes something another
  section already describes (e.g. it corrects an earlier assumption),
  update that section in place rather than leaving two conflicting
  statements standing.

## Escaping conventions (this is pandoc-derived markdown)

Apostrophes → `\'`. Double quotes → `\"`. Em dashes → plain `---`
(never escaped). Match whatever's already in the surrounding text.

## After editing: sanity-check

Confirm no section header was accidentally duplicated or displaced:

```bash
grep -nE "^[0-9]{1,2}\\\\\. [A-Z]" "docs/AI Coding Agentic Solution - Specification (Rev 6).md"
```

Every top-level section (1–25) should appear exactly twice (once in
the table of contents, once as the real heading), in order.

## Last step

If the revision changes anything a deliverable brief quotes or points
to (`docs/deliverables/*.md`), the affected briefs are now stale — run
the `sync-deliverable-briefs` skill next; don't leave that for later.
