# D9 — Gates: Plan-Approval/Change-Review Flow, Autonomy Levels, Escalation

The human-in-the-loop machinery (master spec Sec. 12, 12.1, 9.2, 9.3,
17.2): autonomy-level configuration (L0-L3), gate approver resolution,
self-approval disallowed by construction, escalation on approver
unavailability, L2's batched-review cadence, and L3's structural floor
against ever merging or deploying. See
`wave2-D9-gates-governance.md` for the brief this implements, and the
final agent report for the acceptance-criteria checklist, what's mocked
vs. real, and known assumptions.

D9 is a pure consumer of D1 (job dispatcher), D2 (orchestrator), D4
(Jira integration), D3 (index server / CODEOWNERS), and D5 (source
control) — nothing under those services' directories is modified here.

## Layout

```
services/gates/
  src/gates/
    autonomy.py            L0-L3 gating-behavior table + per-repo/
                            task-class AutonomyConfigStore (config
                            file, not a global constant)
    identity.py             IdentityResolver interface (the real OIDC
                            integration seam, Sec. 17.3) +
                            MockIdentityResolver (deterministic mock)
    approver.py             Sec. 12.1 default-approver resolution
                            (Jira Assignee, else Reporter) against
                            D4's real JiraClient
    codeowners.py           change-review's CODEOWNERS requirement,
                            via D3's real RepoIndex.ownership engine
    self_approval.py        the nominal-type GateClearance: the ONLY
                            place a gate's self-approval + CODEOWNERS
                            checks are evaluated, structurally
    business_calendar.py     weekend-aware business-day SLA math
    escalation.py            open-gate SLA tracking + escalation to a
                            configured backup approver, audited
    batching.py              L2's 5-stories/24h/never-spans-repos
                            batch-review engine
    pr_gate.py               L3's floor: opens a PR via D5's real
                            SourceControlService and stops -- no
                            merge/deploy method exists anywhere here
    audit.py                 durable, append-only gate audit log
    gate_service.py           GatesService: composes all of the above
                            into the entry point a real caller uses
                            instead of calling D2's approve_plan/
                            approve_change_review directly

  config/autonomy.example.json   example per-repo/task-class config

  tests/                    one pytest module per concern (see the
                            final agent report for the acceptance-
                            criteria -> test-file mapping)
```

## Setup

```bash
cd services/gates
python3 -m venv .venv
# kms-boundary first: issue-tracker's own pyproject.toml depends on it
# (its real Sec. 17.3 KMS-unwrap path) -- undocumented here until a
# Docker Compose packaging pass (D14) hit the resulting install failure
# and traced it back.
.venv/bin/pip install -e ../kms-boundary
.venv/bin/pip install -e '.[dev]' \
  -e ../run-registry \
  -e ../orchestrator \
  -e ../issue-tracker \
  -e ../index-server \
  -e ../source-control
```

`run-registry` (F2), `orchestrator` (D2), `issue-tracker` (D4),
`index-server` (D3), and `source-control` (D5) are installed from their
local paths, not a package index — this deliverable imports and drives
their actual, real code (`JiraClient`, `RepoIndex`, `SourceControlService`,
...), never a reimplementation of any of it.

## Running the tests

```bash
cd services/gates
.venv/bin/python -m pytest tests/ -v
```

No live Jira org, GitHub organization, or OIDC identity provider is
required or contacted. Tests exercise D4's real `JiraClient` against
its own local Jira mock server, D5's real `SourceControlService`
against its own local GitHub mock server, and D3's real CODEOWNERS
engine against a real on-disk fixture repository — reusing each
dependency's own already-built mock (via direct import of its module
file) rather than building a parallel one.

## What's mocked vs. real

There is no live OIDC identity provider, Jira org, or GitHub
organization in this environment. See each module's own docstring for
what's genuinely real (all gating/escalation/batching/self-approval
*logic*, D4's Jira HTTP client, D5's GitHub App client, D3's CODEOWNERS
engine) versus the one external boundary mocked per the repo's own
convention (`gates.identity.MockIdentityResolver`, documented as the
seam a real OIDC provider plugs into later) — and the final agent
report for the short version.
