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
    ollama_backend.py     OllamaAgentBackend: real AgentBackend client for
                         a self-hosted Ollama instance serving
                         `ornith-1.5-35b-a3b`, validated against a local
                         mock Ollama HTTP server (see "OllamaAgentBackend"
                         below)
    anthropic_backend.py   (New, Rev 9) AnthropicAgentBackend: real client
                         for Anthropic's Messages API (Section 13.8)
    openai_backend.py      (New, Rev 9) OpenAIAgentBackend: real client
                         for OpenAI's Chat Completions API (Section 13.8)
    bedrock_backend.py     (New, Rev 9) BedrockAgentBackend: real client
                         for Amazon Bedrock's Converse API, hosting
                         MiniMax M2.5 among other models (Section 13.8)
    backend_factory.py      (New, Rev 9) create_agent_backend(vendor, ...):
                         the config-driven seam that selects among all
                         five AgentBackend implementations above (see
                         "Selecting an inference vendor" below)
    docker_sandbox.py       (New, Rev 9) DockerContainerSandboxRuntime:
                         Section 10.3's ephemeral-per-task-container
                         sandbox tier for the Docker Compose deployment
                         mode (Section 14.16) -- a real fourth
                         SandboxTier, not a placeholder (see "Sandbox
                         tiering" below)
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
                         structural placeholders, and (New, Rev 9)
                         SandboxTier.DOCKER_CONTAINER dispatching to
                         docker_sandbox.py's real implementation
  tests/                          one pytest module per concern; see the
                         final agent report for the acceptance-criteria
                         -> test-file mapping
    mock_ollama_server.py         local stdlib HTTP server mimicking
                         Ollama's native /api/chat response shape, for
                         test_ollama_backend.py
    mock_anthropic_server.py      (New, Rev 9) same idiom, for
                         test_anthropic_backend.py
    mock_openai_server.py         (New, Rev 9) same idiom, for
                         test_openai_backend.py
    fake_bedrock_client.py        (New, Rev 9) hand-built fake
                         bedrock-runtime client (moto 5.2.3, the version
                         already in use elsewhere in this repo, does not
                         implement Bedrock's Converse API), for
                         test_bedrock_backend.py
    test_docker_sandbox.py        (New, Rev 9) real tests against a real
                         local Docker daemon (skipped, not disabled,
                         where none is reachable)
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

There is no live LLM API endpoint, and no live Anthropic/OpenAI/Bedrock
account, in this environment, and no real Firecracker/gVisor runtime
available for the multi-tenant cloud deployment's own sandbox tiers. See
`model_backend.py`'s and `sandbox.py`'s module docstrings for exactly
what is genuinely enforced/real in this pass versus a structural
placeholder for D6/infra to back later -- and the final agent report for
the short version. **One exception, new in Rev 9**: the Docker Compose
deployment mode's own sandbox tier (`docker_sandbox.py`,
`SandboxTier.DOCKER_CONTAINER`) is genuinely real, not a placeholder --
it runs a real container against a real local Docker daemon, not a
subprocess standing in for one. See "Sandbox tiering" below.

## Selecting an inference vendor (New, Rev 9)

Master spec Section 13.8 ("Multi-Vendor API-Key Inference"): the Docker
Compose deployment mode (Section 14.16) has no self-hosted GPU to serve
the pinned reference model from, so it delegates inference to an
external, API-key-authenticated vendor instead -- selected per
deployment, never hardcoded at a call site in `core.py`. Five
`AgentBackend` implementations exist side by side, all built independently
against the same interface and reconciled here:

```python
from orchestrator import create_agent_backend

# Anthropic
backend = create_agent_backend("anthropic", api_key="sk-ant-...")

# OpenAI
backend = create_agent_backend("openai", api_key="sk-...")

# Amazon Bedrock (hosting, among other models, MiniMax M2.5) -- model_id
# has no default; see bedrock_backend.py's module docstring for why.
backend = create_agent_backend("bedrock", model_id="<your account's model id>", region_name="us-east-1")

# Self-hosted Ollama (the multi-tenant cloud architecture's own option)
backend = create_agent_backend("ollama", base_url="http://ollama:11434")

# The deterministic test mock -- what every existing test in this
# package still uses
backend = create_agent_backend("scripted", plans=[...], diffs=[...])
```

Every kwarg is that vendor's own `*BackendConfig` dataclass field name
verbatim (`backend_factory.py`'s docstring explains why: no
renaming/translation layer, so a deployment's config file is a near-
literal transcription of whichever vendor's own config fields it's
filling in) -- see each vendor module's own docstring (`anthropic_backend.py`,
`openai_backend.py`, `bedrock_backend.py`, `ollama_backend.py`) for its
exact fields and defaults. `create_agent_backend("bad-vendor", ...)`
raises `ValueError` naming every supported vendor;
`create_agent_backend("bedrock", ...)` without `model_id` raises a
`ValueError` explaining exactly why that one field has no default,
rather than a confusing `TypeError` from inside the dataclass
constructor.

**Credential handling, all four vendors**: every API key/credential is a
plain constructor argument, never read from an environment variable
inside any backend class, and never interpolated into any exception
message, log line, or `repr()` that class raises/produces -- proven by a
dedicated test in each vendor's own test file (see each module's
docstring for specifics). `create_agent_backend` itself never logs or
persists the kwargs it's given either.

## Sandbox tiering (Section 10.1, and Section 10.3 New Rev 9)

`sandbox.py`'s `SandboxTier` enum now has four members:
`MICROVM`/`GVISOR`/`CONTAINER` (Section 10.1, the multi-tenant cloud
architecture's tiers -- `MICROVM`/`GVISOR` are structural placeholders
there, see above) and `DOCKER_CONTAINER` (Section 10.3, New Rev 9 --
`default_runtime_for_tier(SandboxTier.DOCKER_CONTAINER)` returns a real
`docker_sandbox.DockerContainerSandboxRuntime`, genuinely isolating each
unit of agent-generated code in its own short-lived, single-use Docker
container rather than delegating to the plain-subprocess mechanics the
other three tiers share). This tier is explicitly scoped to the
single-tenant Docker Compose deployment mode (Section 14.16) -- it must
never be selected in the multi-tenant cloud architecture, since a
container's isolation floor (a shared host kernel) is weaker than
Section 10.1's microVM tier is meant to guarantee there (see master
spec's Risk Register, "Container-sandbox isolation floor lower than the
fleet's microVM tier"). See `docker_sandbox.py`'s own module docstring
for exactly how resource limits (Docker's own `--ulimit`/`--memory`/
`--cpus`/`--pids-limit`, plus this module's own wall-clock enforcement)
and egress control (the existing `AllowlistProxy`, reached across the
container boundary via `host.docker.internal`, or `--network none` when
no allowlist is configured at all) are real, working mechanisms, not
placeholders -- and `tests/test_docker_sandbox.py` for the tests that
prove it against a real local Docker daemon (skipped, not silently
disabled, wherever none is reachable).

## `OllamaAgentBackend`

`ollama_backend.py` is a real `AgentBackend` implementation (see
`model_backend.py`'s `AgentBackend` interface) that speaks to a
self-hosted [Ollama](https://ollama.com) instance serving the pinned
model `ornith-1.5-35b-a3b` (35B total / ~3B active MoE, Q4_K_M, 256K
context, text+image input) -- a real, already-published Ollama library
model.

**Endpoint choice.** It calls Ollama's native `/api/chat`, not the
OpenAI-compatible `/v1/chat/completions` surface Ollama also exposes.
Both are real supported endpoints; native `/api/chat`'s `format` field
accepts a full JSON Schema, which Ollama's decoder uses to constrain
generation so the emitted JSON structurally conforms to the schema
(required keys, correct types/enums) -- not just "some parseable JSON"
the way the OpenAI-compatible surface's `response_format: {"type":
"json_object"}` toggle guarantees. That is the cleaner structured-output
technique for turning a chat response into the exact `PlanOutput`/
`DiffOutput` dataclass shape this deliverable needs, at the cost of
being Ollama-specific rather than a generic OpenAI-compatible client --
an acceptable trade-off since the pinned model is served by Ollama
specifically. See the module docstring for the full reasoning.

**What's real:**
- The HTTP client (stdlib `urllib`, matching
  `services/source-control/src/source_control/github_client.py`'s
  idiom -- no new dependency; this package's `pyproject.toml` has no
  `httpx`/`requests`).
- Request construction for `author_plan`/`re_plan`/`implement_subtask`:
  each builds a real chat request (system prompt + a JSON-serialized
  `run_context`, plus `feedback`/`subtask` where relevant) with a JSON
  Schema `format` matching `PlanOutput`/`DiffOutput` exactly.
- Response translation: parsing `message.content` as JSON and mapping it
  field-for-field into the exact `PlanOutput`/`DiffOutput` (and nested
  `AcceptanceCriterion`/`SubTask`) dataclass shape, with tuple
  conversion, not a reimplementation of those dataclasses.
- Error handling: three distinct, real exception types --
  `ModelNotReadyError` (connection refused or request timeout, retried
  with exponential backoff up to a configurable `max_retries` before
  raising -- the real deployment scales this backend's GPU node to zero
  when idle, so a cold start is an expected, distinct, caller-retryable
  condition), `OllamaRequestError` (the endpoint responded with an HTTP
  error status -- not retried, since retrying an unchanged bad request
  won't succeed), and `MalformedResponseError` (200 OK but the body
  wasn't valid JSON, or valid JSON that didn't match the required
  schema -- real parsing with a specific exception, never best-effort
  string scraping).
- Config surface: `OllamaBackendConfig` (a frozen dataclass -- `base_url`,
  `model`, `timeout_seconds`, `max_retries`, `retry_backoff_seconds`,
  `temperature`), passed as a plain constructor arg, matching this
  repo's config idiom elsewhere (e.g.
  `tenant_cell.model_diversity.TenantCellModelConfig`) rather than
  reading environment variables inside the class.
- Tests (`tests/test_ollama_backend.py`) against a local mock Ollama
  server (`tests/mock_ollama_server.py`, a real stdlib `http.server`
  mimicking `/api/chat`'s response envelope, same pattern as
  `services/source-control/tests/mock_github_server.py`): successful
  `author_plan`/`re_plan`/`implement_subtask` round trips including the
  JSON-schema-constrained request actually sent, malformed-body and
  malformed-JSON-content handling, a missing-required-key schema
  violation, an HTTP-error-status case, a real connection-refused
  cold-start case (the mock server is stopped mid-test so nothing is
  listening), a real socket-timeout cold-start case (the mock server
  delays its response past the configured timeout), and a
  retry-then-succeed case (a simulated cold start that clears partway
  through the retry budget).

**What a human still needs to do:** deploy a real Ollama instance
serving `ornith-1.5-35b-a3b` (pull the model, expose `/api/chat`) and
point `OllamaBackendConfig.base_url` at it -- this module is real client
code tested against a mock, not a claim that a live model was actually
called from this environment; deploying/scaling the Ollama instance
itself (including the scale-to-zero GPU node behavior `ModelNotReadyError`
is designed around) is infra's/D6's concern, not something this module
manages.
