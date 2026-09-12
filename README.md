# SDLC Auto

An open-source, self-hosted, multi-tenant platform for running AI coding
agents against a real software development lifecycle — intake from a
ticket, research, plan, human approval, implementation, automated
verification, human review, PR. Self-hosted so an organization keeps
control of its code, its models, and its cost; multi-tenant from the
ground up, with a dedicated compute cell (model + orchestrator + sandbox,
no GPU sharing) per tenant rather than a shared pool.

## Status

Active development. Every component below is real, independently-tested
code — not a mockup — but the system has not yet been run end-to-end
against a live cloud account, a real model endpoint, or a real Jira/
GitHub organization. Each service's own README says exactly what's real
versus backed by a local mock at its external boundary, and `CLAUDE.md`
has a full list of what's still open before a real deployment.

## How it fits together

| Component | What it does |
|---|---|
| `infra/` | Cloud-agnostic OpenTofu: network, GPU node pools, model serving, sandbox runtimes, secrets, observability — one module tree, swappable per cloud |
| `services/run-registry` | The source of truth for every run's state — the one thing every other component reads/writes through |
| `services/mcp-stubs` | Versioned MCP tool contracts (index, issue-tracker, source-control, CI) other components build against |
| `services/orchestrator` | The agent runtime: the nine-stage workflow, Skills/Hooks/Subagents, plan artifacts, checkpoints |
| `services/job-dispatcher` | Webhook intake, tenant resolution, capacity requests |
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

## Getting started

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

## Docker Compose deployment

For a single-tenant, single-host deployment (master spec §14.16) rather
than the multi-tenant Kubernetes architecture above:

```bash
cp .env.example .env    # fill in real values -- see the file's own comments
docker compose up -d --build
```

This brings up every service on one Docker network, fronted by a Caddy
reverse proxy (real TLS -- self-signed by default, or a real Let's
Encrypt certificate once `.env`'s `CADDY_DOMAIN` is a real hostname) at
`https://localhost/` (the Fleet Control Dashboard) and
`https://localhost/webhook` (the job dispatcher's webhook endpoint).
`docker compose --profile observability up -d` additionally starts an
optional Prometheus/Grafana/Loki/Tempo stack (§16.4) — nothing else
depends on it.

**Read this before relying on it as a real deployment**: this mode
packages exactly what exists in `services/` today. Only `job-dispatcher`
and `fleet-dashboard` are real, network-reachable HTTP services;
`run-registry` has no server process of its own (every consumer embeds
it as a library against one shared SQLite volume); `index-server` and
`mcp-stubs` speak real MCP but only over stdio, not network; and
`orchestrator`/`gates`/`verification-pipeline`/`issue-tracker`/
`source-control` have no standalone process entrypoint in this codebase
at all — their containers build the image and run that package's own
real test suite as a self-check, then exit. `docker-compose.yml`'s own
top-of-file comment explains each of these honestly, service by
service, along with the one known gap this packaging step could not
close without editing another deliverable's source: job-dispatcher's
capacity provider is still hardcoded to `MockCapacityProvider` (there is
no existing config-driven way to select D1's new `LocalCapacityProvider`
at process startup).

## Contributing

See `CLAUDE.md` for repo conventions (this doubles as the guide
[Claude Code](https://claude.com/claude-code) or any other coding agent
should follow when working in this repo).

## License

Apache License 2.0 — see `LICENSE`.
