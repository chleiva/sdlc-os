# Changelog

All notable changes to this project are documented here. This project
doesn't yet cut versioned releases (see `services/platform-release` for
the release-manager machinery once it starts driving this repo's own
releases) — entries are grouped by the master spec revision that
introduced them instead, matching `CLAUDE.md`'s revision-tagging
convention (`grep "Rev N"` inside the — internal, gitignored — master
spec finds everything a revision touched).

## [Unreleased]

### Added
- Repo housekeeping: CI (`.github/workflows/ci.yml`) running every
  `services/*` package's own test suite, an OpenTofu format check for
  `infra/`, and Docker Compose config validation; `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue/PR templates, and
  Dependabot.
- `deploy/run-worker/` (new): the real, manually-invoked orchestrator
  process entrypoint `CLAUDE.md`'s "Known cross-deliverable gaps" flags
  as missing — `live_run.py` (task typed on the command line) and
  `jira_poll_run.py` (task pulled from a real Jira Cloud story, with a
  real async human-in-the-loop gate via Jira comment/reply polling, not
  a blocking prompt). Both share one real pipeline: real GitHub App
  clone/branch/worktree, a real multi-turn Bedrock tool-using
  implementation loop (`BedrockToolUseAgentBackend`) that actually
  writes files, real `pytest`/`ruff`/`mypy` verification, a real gate,
  and a real PR. See its own README for exact setup and current,
  honestly-stated limitations.
- `services/orchestrator`: a real, targeted `edit_file` (str_replace-
  style) tool alongside `write_file` in the Bedrock implementation
  loop, so a small change no longer requires regenerating an entire
  file — the model is instructed to prefer it for any existing file.
- `services/orchestrator`: real Bedrock model fallover
  (`BEDROCK_FALLBACK_MODELS`), alongside the existing region fallover,
  for a persistently degraded model rather than just a degraded
  region — tries every configured region for the preferred model
  first, only downgrading once that's exhausted.
- `services/orchestrator`: every vendor `AgentBackend`'s plan prompt now
  states the real, enforced per-story-size diff-size ceiling (§9.4),
  generated from the same numbers the Section 9.3 size checkpoint
  itself enforces; the Bedrock implementation loop is told a real
  pacing target and nudged mid-loop as it approaches its budget,
  instead of only finding out from a checkpoint after the whole diff is
  already written.
- `services/orchestrator`: real per-call timing logged for every
  Bedrock Converse call (success and each retry/region/model
  fallover), so a slow run is diagnosable from its logs.

### Fixed
- A real, severe bug where generated code was never actually committed
  to git (`BedrockToolUseAgentBackend` never ran `git commit`) — every
  prior "successful" PR silently contained none of the actual generated
  changes.
- Bedrock Converse calls now retry with real exponential backoff and
  real region fallover instead of crashing the whole run on a single
  transient timeout/throttle.
- A repeatedly-failing implementation attempt (e.g. a plan authoring a
  subtask the agent structurally cannot complete) used to retry
  silently forever, once per external poll tick, never surfacing to a
  human. It now counts toward the same Section 9.3 "stuck" checkpoint a
  repeated verification failure already uses, and pauses for a real
  human decision once the retry budget is exhausted.

## Revision 9

- Two deployment modes side by side: the original multi-tenant,
  self-hosted-GPU cloud architecture (`infra/`), and a single-tenant
  Docker Compose stack (`docker-compose.yml`, `deploy/`, D14) that
  delegates model inference to an external API-key vendor (§13.8).
- `services/orchestrator`: `create_agent_backend()` and four vendor
  `AgentBackend` implementations (Anthropic, OpenAI, Bedrock, Ollama)
  for API-key inference, plus a `SandboxTier.DOCKER_CONTAINER`
  ephemeral-per-task sandbox tier (§10.3) alongside the microVM/gVisor
  tiers.
- `services/job-dispatcher`: `LocalCapacityProvider`, an
  always-available capacity provider for deployments with no GPU pool
  to request from.
- `services/kms-boundary` (new): per-tenant KMS envelope encryption
  (§17.3) — closes a real gap D10's audit found; consumed by
  `services/source-control` and `services/issue-tracker`.

## Revisions 1–8

All 13 original deliverables across Wave 0 (F1 IaC, F2 Run Registry, F3
MCP stubs) and Wave 1 (D1 job-dispatcher, D2 orchestrator, D3
index-server, D4 issue-tracker, D5 source-control, D6 tenant-cell, D7
verification-pipeline, D8 fleet-dashboard) through Wave 2 (D9 gates, D10
security-hardening, D11 observability) and Wave 3 (D12 platform-release,
D13 evaluation-harness) — the initial real, independently-verified
implementation of the master specification. See each service's own
README for what's real versus mocked at its own external boundary, and
`CLAUDE.md`'s "Known cross-deliverable gaps" for what's still open.
