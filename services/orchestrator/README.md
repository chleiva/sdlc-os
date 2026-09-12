# D2 — Agent Orchestrator Core

The nine-stage agent runtime (master spec Section 5): the orchestrator
process that runs plan -> implement -> checkpoint -> gate inside one
tenant's compute cell, loading Skills, enforcing Hooks, spawning
Subagents, and writing every stage transition through F2's real
`RegistryService`. See `wave1-D2-orchestrator-core.md` for the brief this
implements, and the final agent report for the full acceptance-criteria
checklist, what's mocked vs. real, and known gaps/assumptions.

## Layout

```
services/orchestrator/
  src/orchestrator/
    core.py             the Orchestrator class: the nine-stage state
                         machine, gates, Section 9.3 checkpoint
                         pause/resume via structured elicitation
    model_backend.py     AgentBackend interface (real-model integration
                         seam) + ScriptedAgentBackend (deterministic mock)
    verification.py      VerificationRunner interface (D7 integration
                         seam) + ScriptedVerificationRunner (mock)
    checkpoints.py        Section 9.4 default budgets + Section 9.3
                         checkpoint-trigger computation
    plan_artifact.py       Section 9.5 structured plan artifact: JSON
                         Schema + generator + durable per-run store
    schema/plan_artifact.schema.json   the versioned plan-artifact schema
    progress.py            durable per-run implementation progress
                         (completed subtasks, accumulated diff, spend)
    hooks.py                the Hook chain (PreToolUse/PostToolUse/
                         on-stop) + DestructiveCommandHook/
                         ScopeBoundaryHook/SecretRedactionHook
    skills.py                ToolInvoker, Skill, Subagent (incl. the
                         isolated-context reviewer spawn)
    mcp_clients.py             real MCP stdio clients against F3's stub
                         servers
    worktree.py                 git-worktree-per-agent isolation
                         (Section 8.1), mirroring D5's technique
    sandbox.py                   Section 10.1 sandbox tiering: real
                         subprocess resource limits + egress allowlist
                         proxy, tier-selection policy, microVM/gVisor
                         structural placeholders
  tests/                          one pytest module per concern; see the
                         final agent report for the acceptance-criteria
                         -> test-file mapping
```

## Setup

```bash
cd services/orchestrator
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]' -e ../run-registry
```

`run-registry` (F2) is installed from its local path, not a package
index -- it is a real, direct dependency (this deliverable imports and
drives its actual `RegistryService`, never a reimplementation of it).

## Running the tests

```bash
cd services/orchestrator
.venv/bin/python -m pytest tests/ -v
```

Several tests spawn F3's real stub MCP servers
(`services/mcp-stubs/*/server.py`) as real stdio subprocesses -- no
network access needed, but `services/mcp-stubs/` must exist as a sibling
directory (it does, as F3's own deliverable). One sandbox test is
platform-conditionally skipped on macOS (`RLIMIT_AS` is a documented
no-op on Darwin); it runs for real on Linux.

## What's mocked vs. real

There is no live LLM API endpoint in this environment (D6 doesn't exist
as a running service here) and no real Firecracker/gVisor runtime
available. See `model_backend.py`'s and `sandbox.py`'s module docstrings
for exactly what is genuinely enforced/real in this pass versus a
structural placeholder for D6/infra to back later -- and the final agent
report for the short version.
