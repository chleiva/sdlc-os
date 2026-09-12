# Changelog — F3 MCP Tool Contracts

Every entry names which schema file(s) changed and its new `version`.
A schema version bump is a reviewed change (master spec Sec. 7.5) — never
edit a schema's tool shapes without adding an entry here.

## [Unreleased]

Nothing yet.

## 1.0.0 — 2026-09-12

Initial versioned schemas and stub servers for all four MCP servers named
in master-spec Sec. 7.6 that this deliverable owns.

- `index/schema/index.schema.json` — `1.0.0`. Tools: `find-definition`,
  `find-references`, `find-callers`, `search`, `get-file-module-summary`,
  `get-ownership-metadata`.
- `issue-tracker/schema/issue-tracker.schema.json` — `1.0.0`. Tools:
  `create-epic`, `create-story`, `get-issue`, `transition-status`,
  `post-comment`.
- `source-control/schema/source-control.schema.json` — `1.0.0`. Tools:
  `create-branch-worktree`, `open-pr`, `get-pr-check-status`,
  `get-file-contents`, `list-files`.
- `ci/schema/ci.schema.json` — `1.0.0`. Tools: `trigger-run`,
  `get-run-status-result`.

Every tool: JSON Schema 2020-12 input/output schemas, `tenant_id` required
on every input, a success/`empty`/named-error (`not-found`,
`permission-denied`, `rate-limited`, `upstream-unavailable`) result
envelope. See `README.md` for the envelope shape and the interpretive
decisions made where the master spec names the contract shape but not
every tool's exact field list.
