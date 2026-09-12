# SDLC Auto

An open-source, self-hosted, multi-tenant platform for running AI coding
agents against a real software development lifecycle — intake from a
ticket, research, plan, human approval, implementation, automated
verification, human review, PR. Self-hosted so an organization keeps
control of its code, its models, and its cost; multi-tenant from the
ground up, with a dedicated compute cell (model + orchestrator + sandbox,
no GPU sharing) per tenant rather than a shared pool. It ships two
deployment modes: a single-tenant Docker Compose stack for trying it
quickly on one machine, and the full multi-tenant cloud architecture for
production (see "Quick start" and "Multi-tenant cloud deployment" below).

## Status

Active development. Every component below is real, independently-tested
code — not a mockup — but the system has not yet been run end-to-end
against a live cloud account, a real model endpoint, or a real Jira/
GitHub organization. Each service's own README says exactly what's real
versus backed by a local mock at its external boundary, and `CLAUDE.md`
has a full list of what's still open before a real deployment,
including two gaps the Quick start section below states plainly.

## Quick start: Docker Compose

The fastest way to actually try the System is the single-tenant Docker
Compose deployment (master spec §14.16), which runs every component on
one Docker host and delegates model inference to an external API-key
vendor rather than self-hosting a GPU (§13.8):

```bash
cp .env.example .env
# Fill in .env: TENANT_ID, one inference vendor's credentials
# (ANTHROPIC_API_KEY, OPENAI_API_KEY, the Bedrock block, or a
# self-hosted Ollama's OLLAMA_BASE_URL — see the file's own comments
# and pick exactly one), and GitHub App / Jira details if you want
# those integrations live.
docker compose up -d --build
```

This brings up every service on one Docker network behind a Caddy
reverse proxy (self-signed TLS by default, a real Let's Encrypt
certificate once `.env`'s `CADDY_DOMAIN` is a real hostname) at
`https://localhost/` (the Fleet Control Dashboard) and
`https://localhost/webhook` (the job dispatcher's webhook endpoint).
`docker compose --profile observability up -d` additionally starts an
optional Prometheus/Grafana/Loki/Tempo stack — nothing else depends on
it.

**What's real here today, stated plainly — this is not yet a working
end-to-end agent run:**

- `job-dispatcher` and `fleet-dashboard` are real, network-reachable
  HTTP services. job-dispatcher's capacity request is satisfied by the
  new, always-available `LocalCapacityProvider` (§14.16), which this
  deployment selects by default (`JOB_DISPATCHER_CAPACITY_PROVIDER=local`)
  since there's no GPU to provision when inference is delegated to an
  external vendor.
- **`orchestrator` has no real process entrypoint yet.** It's a library
  driven by tests so far — nothing in this codebase calls it at
  runtime. Its container builds the image (including the new Rev 9
  vendor `AgentBackend`s and the Docker-container sandbox tier) and
  runs its own test suite as a self-check, then exits; a real service
  that `job-dispatcher` actually dispatches work to is still open.
- **The Run Registry has no network-reachable server.** Every consumer
  (`job-dispatcher`, `fleet-dashboard`, `orchestrator`, `gates`) uses it
  as an embedded library against one SQLite file. This compose stack
  works around that with a shared Docker volume, not a real Registry
  service.
- `index-server`/`mcp-stubs` speak real MCP, but only over stdio, not
  network, so their containers here are for build/inspection
  completeness, not live traffic on this compose network.
- `orchestrator`/`gates`/`verification-pipeline`/`issue-tracker`/
  `source-control` likewise have no standalone process entrypoint in
  this codebase; each one's container runs that package's own real,
  documented test suite as a build-time self-check and exits, rather
  than staying up as a service.

See `docker-compose.yml`'s own top-of-file comment and each service's
`Dockerfile` for the full, service-by-service version of the above; see
`CLAUDE.md`'s "Known cross-deliverable gaps" for the standing list,
including these two.

## How it fits together

| Component | What it does |
|---|---|
| `infra/` | Cloud-agnostic OpenTofu: network, GPU node pools, model serving, sandbox runtimes, secrets, observability — one module tree, swappable per cloud (the multi-tenant cloud deployment path, below) |
| `docker-compose.yml`, per-service `Dockerfile`s, `deploy/` | The Docker Compose deployment path (§14.16): one command brings up the whole stack on a single Docker host — `deploy/caddy` (reverse-proxy TLS + routing), `deploy/job-dispatcher` (a packaging-only entrypoint/config renderer, not part of job-dispatcher's own source), `deploy/observability` (the optional Prometheus/Grafana/Loki/Tempo profile) |
| `services/run-registry` | The source of truth for every run's state — the one thing every other component reads/writes through |
| `services/mcp-stubs` | Versioned MCP tool contracts (index, issue-tracker, source-control, CI) other components build against |
| `services/orchestrator` | The agent runtime: the nine-stage workflow, Skills/Hooks/Subagents, plan artifacts, checkpoints; Rev 9 added `create_agent_backend()` and four vendor `AgentBackend` implementations (Anthropic, OpenAI, Bedrock, Ollama) for API-key inference (§13.8), plus a `SandboxTier.DOCKER_CONTAINER` ephemeral-per-task sandbox tier (§10.3) alongside the microVM/gVisor tiers |
| `services/job-dispatcher` | Webhook intake, tenant resolution, capacity requests; Rev 9 added `LocalCapacityProvider`, an always-available capacity provider for deployments with no GPU pool to request from |
| `services/tenant-cell` | Per-tenant model serving + compute cell provisioning, spot-interruption handling |
| `services/index-server` | Repository index: symbols, exhaustive references, call graph, semantic search |
| `services/issue-tracker` | Jira integration |
| `services/source-control` | GitHub App integration |
| `services/verification-pipeline` | The automated gate a change must pass before it's eligible for human review |
| `services/gates` | Human-in-the-loop: autonomy levels, approvals, escalation |
| `services/fleet-dashboard` | Read-only Kanban view over the Run Registry |
| `services/security-hardening` | Adversarial security/non-human-identity audit suite |
| `services/observability` | Trace/log/metric correlation across a whole run |
| `services/platform-release` | How the platform's own code ships: CI, canary, rollback |
| `services/evaluation-harness` | Benchmark suites + live production metrics |

## Multi-tenant cloud deployment (production path)

For real multi-tenant SaaS use — many tenants, self-hosted model
economics at scale, per-tenant dedicated compute cells, the full
microVM/gVisor sandbox isolation of §10.1 — deploy the original
any-cloud, Kubernetes-based architecture under `infra/` instead. This
remains the specified path for that case (§14.1–14.15); the Docker
Compose mode above is an addition to it, not a replacement (§14.16).

Each service under `services/` is an independent Python package with its
own virtualenv and test suite — none of them need a live cloud account,
model, or SaaS credential to run their tests. Pick one and go:

```bash
cd services/<name>
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"   # see that service's own README for exact steps —
                                     # a couple need sibling packages installed first
.venv/bin/python -m pytest
```

`infra/` is [OpenTofu](https://opentofu.org/) (`tofu`, not the Terraform
CLI) — see `infra/README.md`.

## Contributing

See `CLAUDE.md` for repo conventions (this doubles as the guide
[Claude Code](https://claude.com/claude-code) or any other coding agent
should follow when working in this repo).

## License

Apache License 2.0 — see `LICENSE`.
