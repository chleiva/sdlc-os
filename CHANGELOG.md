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
