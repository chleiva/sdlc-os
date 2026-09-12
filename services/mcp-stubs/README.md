# F3 — MCP Tool Contracts

Versioned MCP tool schemas, plus runnable stub/mock servers, for the four
MCP servers named in master-spec Sec. 7.6 that this deliverable owns:
**index**, **issue-tracker**, **source-control**, **ci**. (The fifth
server Sec. 7.6 names, the Run Registry, is F2's — see
`wave0-F2-run-registry.md`; F3 only needs to know it exists as one more
MCP-shaped server.)

This deliverable does **not** implement real index/Jira/GitHub/CI
behavior — see `wave0-F3-mcp-contracts.md` for the full non-goals list.
Its job is to fix the interface everyone else builds against.

## Layout

```
services/mcp-stubs/
  _common/            shared runtime code (envelope shape, tenant_id-driven
                       scenario dispatch, generic server wiring) -- imported
                       by every server.py, never duplicated per server
  index/
    schema/index.schema.json
    server.py
  issue-tracker/
    schema/issue-tracker.schema.json
    server.py
  source-control/
    schema/source-control.schema.json
    server.py
  ci/
    schema/ci.schema.json
    server.py
  tests/               one contract-test module per server
  requirements.txt
  pyproject.toml
  CHANGELOG.md
```

## Setup

```bash
cd services/mcp-stubs
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Running a stub server standalone

Each server speaks MCP over **stdio** and needs nothing else running:

```bash
.venv/bin/python3 index/server.py
.venv/bin/python3 issue-tracker/server.py
.venv/bin/python3 source-control/server.py
.venv/bin/python3 ci/server.py
```

### Pointing another deliverable's MCP client at a stub

Configure the client with a stdio transport whose command is this venv's
Python and whose arg is the server script, e.g. for an MCP-client config
block:

```json
{
  "mcpServers": {
    "index": { "command": "/path/to/services/mcp-stubs/.venv/bin/python3",
               "args": ["/path/to/services/mcp-stubs/index/server.py"] },
    "issue-tracker": { "command": "/path/to/services/mcp-stubs/.venv/bin/python3",
                       "args": ["/path/to/services/mcp-stubs/issue-tracker/server.py"] },
    "source-control": { "command": "/path/to/services/mcp-stubs/.venv/bin/python3",
                        "args": ["/path/to/services/mcp-stubs/source-control/server.py"] },
    "ci": { "command": "/path/to/services/mcp-stubs/.venv/bin/python3",
            "args": ["/path/to/services/mcp-stubs/ci/server.py"] }
  }
}
```

Or drive it directly with the official Python MCP SDK client:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

params = StdioServerParameters(command=".venv/bin/python3", args=["index/server.py"])
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("find-definition", {
            "tenant_id": "tenant-acme", "repository": "acme/app",
            "symbol": "process_payment", "origin": {"file": "src/api.py", "line": 12},
        })
        print(result.structured_content)
```

Each server's *advertised* tool list — name, description, `input_schema`,
`output_schema` — is read directly from its `schema/<name>.schema.json`
file at process start (`_common/server_runtime.py`), so the running
process can never drift from the checked-in schema.

## The contract shape (every tool, every server)

Every tool's response is one of exactly three mutually exclusive shapes,
discriminated by an `outcome` field — this is what makes "nothing
matched" impossible to confuse with "the call failed" (master spec Sec.
7.6):

- **`{"outcome": "ok", "data": {...}}`** — success, tool-specific `data`.
- **`{"outcome": "empty", "reason": "...", ...}`** — the call succeeded
  but there was nothing to return/do. Never has `data` or `error`.
- **`{"outcome": "error", "error": {"code": ..., "message": ..., "retryable": ..., "retry_after_seconds": ..., "details": ...}}`**
  — one of the four named conditions: `not-found`, `permission-denied`,
  `rate-limited`, `upstream-unavailable`. Never has `data`.

`is_error` on the MCP `CallToolResult` is `true` exactly when
`outcome == "error"` — `ok` and `empty` are both ordinary (non-error)
results at the protocol level, matching MCP's own convention that
`isError` marks a domain/execution failure, not "nothing matched."

A request with malformed input (fails the tool's own `input_schema`) is
rejected as a protocol-level validation error, distinct from all four
named domain error codes — schema-invalid input isn't a "the server
tried and hit a wall" condition, it's "the request was never well-formed
to begin with."

`tenant_id` is required on **every** tool's input across all four
servers (master spec Sec. 14.13) — there is no tool, in any server, that
assumes a single tenant.

## Driving stub behavior: the tenant_id scenario convention

Because every request already carries `tenant_id`, these stubs reuse it
as the scenario selector too, so the mechanism is identical for every
tool in every server. Any `tenant_id` not listed below gets the
happy-path response:

| `tenant_id`                          | scenario             |
|---------------------------------------|----------------------|
| *(anything else, e.g. `tenant-acme`)* | `ok` (happy path)    |
| `tenant-empty`                        | `empty`              |
| `tenant-error-not-found`              | `not-found`          |
| `tenant-error-permission-denied`      | `permission-denied`  |
| `tenant-error-rate-limited`           | `rate-limited`       |
| `tenant-error-upstream-unavailable`   | `upstream-unavailable` |

These constants live in `_common/scenarios.py` (`ALL_SCENARIO_TENANTS`)
and are imported by both the servers and the contract tests, so the two
can never quietly disagree about what a given `tenant_id` should produce.

## Versioning (master spec Sec. 7.5: pin per deployment, review every upgrade)

- Each schema file carries a top-level `"version"` field (semver) and a
  stable `"$id"`. The running server reads its `version` from the same
  file and reports it as its MCP server `version` — there is no second
  place a version number is hand-copied.
- A schema change (adding a tool, changing required/optional inputs,
  changing a result shape, adding an error condition) is a **reviewed,
  non-silent event**: bump the file's `version`, add a `CHANGELOG.md`
  entry describing what changed and why, and call it out in the PR — the
  same discipline the master spec applies to the MCP protocol version
  itself (Sec. 7.5) and to a platform release (Sec. 14.15). Never
  hand-edit a schema's tool shapes without bumping `version`.
- A deployment pins one schema version per server; upgrading which
  version a deployment runs against is itself a reviewed change, not an
  automatic pickup of whatever is newest in this directory.
- These schemas started at `1.0.0` for all four servers (initial
  creation, see `CHANGELOG.md`).

## Tests

```bash
cd services/mcp-stubs
.venv/bin/python3 -m pytest -q
```

One module per server under `tests/`
(`test_index_contract.py`, `test_issue_tracker_contract.py`,
`test_source_control_contract.py`, `test_ci_contract.py`). Each module:

- Confirms both `input_schema` and `output_schema` for every tool are
  valid JSON Schema 2020-12 (`Draft202012Validator.check_schema`).
- Confirms the running server's advertised tool list matches the schema
  file exactly (no drift between file and process).
- Confirms every tool's input requires `tenant_id`.
- Exercises **every tool's happy path, empty-result path, and all four
  named error conditions** (`ok`/`empty`/`not-found`/`permission-denied`/
  `rate-limited`/`upstream-unavailable` — six scenarios × every tool),
  asserting the response validates against that tool's own
  `output_schema` and carries the right `outcome`/`error.code`/`is_error`.

At last run: **122 passed** (index 6 tools, issue-tracker 5, source-control
5, ci 2 — 18 tools × 6 scenarios = 108, plus 14 structural/shape-specific
tests).

Another deliverable's agent (D3/D4/D5/D7) should be able to copy one of
these test modules as a template, point `SERVER`/`BASE_ARGS` at its own
real implementation once that exists, and reuse `_helpers.py`'s
`call_tool`/`assert_matches_contract` unchanged to prove it is still
speaking the same contract.

## Known limitations / interpretive decisions (flag for a human)

The master spec quotes a contract shape (required/optional inputs,
success shape, distinct empty shape, four named errors) but doesn't spell
out every tool's exact field list or exactly how "empty" should read for
a *write* operation (create/transition/post/trigger). The following calls
were made to satisfy the letter of the brief's acceptance criteria
without inventing scope; a human should sanity-check them against how
the real Jira/GitHub/CI integrations (D4/D5/D7) actually behave:

- **Tool splitting.** The brief's bullet prose sometimes names two
  operations in one line. Implemented as separate tools:
  `create-epic`/`create-story`, `get-file-contents`/`list-files`. Kept as
  one tool (an atomic operation per spec Sec. 8.1): `create-branch-worktree`.
  Combined into one tool (poll returns both): `get-pr-check-status`,
  `get-run-status-result`.
- **"Empty" for write tools.** For a pure query (`find-references`,
  `search`, `get-issue`, `get-pr-check-status`, `list-files`, ...) "empty"
  means "nothing matched." For a write tool (`create-epic`,
  `transition-status`, `post-comment`, `open-pr`, `trigger-run`, ...)
  there's no spec text describing what "empty" means, since these aren't
  literally search operations. Each write tool's empty case was given a
  concrete, realistic no-op narrative (documented on the tool's
  `output_schema` description and in each `server.py`) — e.g.
  `transition-status` is `empty` when the issue is already in the target
  status; `create-epic`/`create-story` are `empty` on an idempotent
  duplicate-summary collision; `trigger-run` is `empty` when the resolved
  scope has no runnable jobs. These are reasonable, but they're this
  deliverable's invention, not the master spec's — worth a second look
  before D4/D5/D7 build against them as gospel.
- **`get-issue`'s empty case** is the least natural fit of the six
  (a keyed single-resource fetch doesn't have an obvious "nothing
  matched" state distinct from not-found). Modeled as "the key resolves
  but the issue has been archived, content unavailable" — distinct from
  `not-found` (key never existed) and `permission-denied` (access
  explicitly refused). Flag this one specifically if D4 disagrees.
- **Auth is out of scope for the stub.** Spec Sec. 7.5 requires
  OAuth/OIDC-hardened auth for any real MCP server this System connects
  to. These are canned-response stubs with no real upstream to
  authenticate against, so they trust `tenant_id` directly rather than
  validating a credential — that is a deliberate stub simplification, not
  a claim that the contract doesn't need auth. A real D3/D4/D5/D7
  implementation still owes Sec. 7.5's OAuth/OIDC hardening; nothing here
  should be read as satisfying it.
- **MCP SDK/protocol version.** `mcp==2.2.0` is pinned because it's the
  first PyPI release built against the mid-2026 MCP rewrite Sec. 7.5
  describes (Tasks extension graduated to first-class, deprecated
  Roots/Sampling/Logging, stricter 2020-12 tool schemas). Every
  `output_schema` in this deliverable also carries a redundant top-level
  `"type": "object"` alongside its `oneOf` — required for wire
  compatibility with MCP protocol versions older than the newest one this
  SDK supports; harmless (and unnecessary) once every caller is on the
  newest version. Tasks aren't exercised by these stubs since every canned
  call returns instantly; a real implementation of a genuinely
  long-running operation (e.g. a large `find-references` on a very large
  index) should use the Tasks extension per Sec. 7.5, not a synchronous
  call.
- **Deployment scoping (Sec. 14.13)** is a schema/process-topology note,
  not something a stdio stub can demonstrate physically: index,
  source-control, and CI are meant to be deployed per tenant; issue-tracker
  may be one shared, tenant-scoped deployment. All four schemas carry a
  `"tenant_scoping"` field documenting which, and every tool still
  requires `tenant_id` regardless — so swapping either server between
  per-tenant and shared deployment never requires a contract change.
