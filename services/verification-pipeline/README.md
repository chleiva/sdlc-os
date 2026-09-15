# D7 — Verification Pipeline

Implements master-spec Section 11's seven verification layers (the
automated gate a change must pass before it's eligible for the D9 human
change-review gate), plus flaky-test detection, a bounded retry budget,
and the "tests green is necessary, not sufficient" rule. See
`docs/deliverables/wave1-D7-verification-pipeline.md` for the brief this
was built against.

## Layout

```
services/verification-pipeline/
  schema/plan-artifact.schema.json   D7's own interim Sec. 9.5 plan-artifact schema (see below)
  src/verification_pipeline/
    plan_artifact.py                 loader/validator for the schema above
    budgets.py                       Sec. 9.4 budgets + D7's own retry-count addition
    ci_client.py                     real MCP client for F3's `ci` stub
    index_client.py                  real MCP client for D3's real index server
    local_pytest.py                  real pytest subprocess + JUnit-XML parsing
    flaky.py                         re-run-on-failure flaky/genuine classification
    report.py, pipeline.py           VerificationReport + retry-budget orchestration
    layers/                          one module per Sec. 11.1 layer (1-7)
  fixtures/
    target_repo/                     small fixture repo for layers 1-4 (real lint/type/
                                      security violations, passing/failing/new tests)
    flaky/                           fixed-schedule flaky test + a genuine-failure test
    regression/                      baseline.json + a good and a regressed real implementation
    plans/                           example plan artifacts (valid + one missing a test mapping)
  tests/                             one pytest module per acceptance criterion / layer
```

Layer 7 reuses D3's real fixture repo directly:
`services/index-server/tests/fixtures/multi_pkg_repo` (via D3's real MCP
server, not copied).

## Running the tests

```bash
cd services/verification-pipeline
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # includes pytest==9.1.1
.venv/bin/python3 -m pytest -q
```

At last run: **56 passed** (~12s; several tests spawn real subprocesses —
ruff, mypy, bandit, and two real MCP servers over stdio — so this is
slower than a pure-unit-test suite by design).

No network access is required to run the tests: `pip install` needs it
once, up front, but the test run itself only spawns local subprocesses.

## What's real vs. mocked/placeholder, and why

| Layer | What's real | What's mocked/placeholder |
|---|---|---|
| 1. Existing test suite | Real MCP round-trip to F3's `ci` stub (`trigger-run` / `get-run-status-result`); real `pytest` subprocess run + JUnit-XML parsing for per-test identity | F3's CI contract itself is a stub (no live CI in this environment, as the brief anticipates) — see "CI contract's aggregate-only schema" below |
| 2. Acceptance-criteria mapping | Real validation of the plan artifact's map; the mapped tests are actually executed (not trusted on the map's word) | — |
| 3. Static analysis | Real `ruff` and real `mypy`, pip-installed, run as real subprocesses against real fixture violations | — |
| 4. Security scanning | Real `bandit` (SAST) against a real insecure-code fixture; a real, deterministic, offline dependency-version-vs-known-CVE-pattern check (SCA) | `KnownCVEPatternDependencyScanner`'s vulnerability table is a small, illustrative, hand-curated set (real CVE IDs, e.g. CVE-2020-14343 for PyYAML < 5.4) — not a live feed. `pip-audit` is genuinely installed and `security_scan.run_pip_audit` really shells out to it, but it is **not** in the default scanner list or in any deterministic test, because it queries a live vulnerability database (network-dependent, non-reproducible over time). No specific enterprise SAST/SCA tool (Snyk, Semgrep Enterprise, etc.) is wired — `SecurityScanner` is the seam for one. |
| 5. Isolated-context review | Real, tested isolation property: `ReviewerBackend.review`'s signature structurally cannot accept an implementer's conversation object; a runtime test proves mutating the implementer's transcript after building the reviewer's context never reaches it | No live LLM in this environment (same "real orchestration, mocked model call" discipline as D2) — `MockReviewerBackend`/`AdversarialReviewerBackend` are deterministic marker-matchers, not model judgment |
| 6. Behavioral/regression check | Real code (`good_impl.py`/`regressed_impl.py`) is actually executed; the metric is a genuine operation count (not canned) | The metric is deliberately **not** wall-clock time — timing is inherently non-deterministic and would undermine the flaky-vs-genuine distinction Sec. 11.3 asks for elsewhere. A production deployment measuring real service latency would use wall-clock/contract baselines instead. |
| 7. Cross-codebase completion check | Real MCP call to **D3's actual, already-built index server** (`services/index-server/src/index_server/server.py`, spawned as a real subprocess, real stdio MCP protocol) against D3's real fixture repo | — nothing mocked; this is the most "real" layer, per the brief's emphasis |
| Flaky detection | Real re-run logic against a real, fixed-schedule flaky fixture (a counter file, not timing-based) | The fixture's determinism mechanism (a counter file) is a test-harness device, not a claim that all real-world flakiness is this predictable |
| Retry budget | Real bookkeeping (`RetryBudget`, `VerificationPipeline`) | — |

## Plan-artifact schema reconciliation (flag for D2 — still not done)

**D2 (the orchestrator) has since landed, with its own real, independently-
built schema — `services/orchestrator/src/orchestrator/schema/plan_artifact.schema.json`
(D2's `plan_artifact.generate_plan_artifact`) — and the divergence this
section originally flagged as a future risk is real, confirmed, and
still unreconciled.** `schema/plan-artifact.schema.json` here in D7 and
`plan_artifact.py` remain **D7's own JSON Schema**, built directly from
master-spec §9.5's prose before D2 existed to import from — not
D2's schema, and not reconciled with it since.

**Confirmed differences, comparing the two real schemas directly:**

- Naming: D2 uses `subtask_graph.subtasks` (not `sub_task_graph`) and
  `declared_scope.in_scope`/`out_of_scope` (same shape as this schema's
  `declared_scope`, different top-level key nesting in places).
- **D2 combines the criteria list and verification map into one array**
  — `acceptance_criteria_map`, each entry carrying `criterion_id`/
  `description`/`verification_tests` together — rather than this
  schema's two separate, `criterion_id`-joined arrays
  (`acceptance_criteria` + `acceptance_criteria_verification_map`).
  This answers the open question the original version of this section
  posed: D2 chose the combined shape.
- D2 ties a plan artifact to its human-readable source via
  `source_plan_hash` (a content hash), not a `plan_document_ref`
  pointer — a different mechanism achieving the same §9.5 intent.
- D2's `risk.budget` is baked into the artifact at generation time from
  a resolved `checkpoints.Budget` (matching this schema's own
  "pinned at approval time" assumption), with `risk.story_size`/
  `risk.cross_cutting_or_high_risk`/`risk.tier` alongside it.

**Reconciliation still has not happened.** `real_verification_runner
.RealVerificationRunner` (D2's own integration of D7, in
`services/orchestrator`) sidesteps the question entirely rather than
resolving it: it scopes its two real layers off `RunProgress
.files_touched` — the actual files a real `git diff` shows changed —
never off either schema's own `declared_scope`. So the two schemas can
keep silently diverging further with no consumer ever forced to notice.
D7's own layers 2 and (indirectly) the retry/budget logic still consume
`plan_artifact.py`'s schema here, unchanged. Swapping to D2's real
schema should only require changing `plan_artifact.py`'s field lookups
(`criterion_id`, `test_ids`, etc.) — the layer functions themselves
(`acceptance_mapping.py`) take a plain `dict` and would need the same
kind of small adapter either way. This is a genuine, still-open gap
worth a human decision, not merely a documentation nit.

## Other spec ambiguities / interpretive decisions a human should confirm

1. **Retry-attempt count is D7's own default, not a spec number.**
   Master-spec §9.4 pins wall-clock/cost/size-checkpoint budgets per
   story size (S/M/L) but does not pin a specific *retry-attempt count*
   for §9.3's "stuck checkpoint". `budgets.py`'s `RETRY_ATTEMPTS_BY_SIZE`
   (S=2, M=3, L=4) is D7's own reasonable placeholder, flagged the same
   way F3's own README flags its interpretive decisions. A human should
   confirm or override these.

2. **F3's CI contract is aggregate-only** (`tests.total/passed/failed`
   counts — no per-test identity, per `services/mcp-stubs/ci/schema/ci.schema.json`).
   Distinguishing a *named* pre-existing failure from a new regression,
   and re-running one specific test to check flakiness, both need
   per-test identity the wire contract doesn't carry. D7 supplies that
   detail by running `pytest` directly (`local_pytest.py`) alongside the
   real CI-contract call (which still happens, and is asserted on, for
   audit-trail/integration purposes). A real deployment's CI likely
   already reports per-test detail (JUnit XML, etc.); if/when F3's `ci`
   schema is extended to carry it, `existing_tests.py` should switch to
   reading it from the CI response rather than running pytest itself —
   flagging this now rather than silently reinventing test execution.

3. **"Empty" plan-artifact edge cases.** §9.5 doesn't specify what
   happens if `acceptance_criteria` is present but
   `acceptance_criteria_verification_map` is entirely absent (vs. present
   but empty) — this implementation treats both the same
   (`criteria_missing_tests` returns every criterion as missing either
   way). Worth confirming against D2's actual generation behavior.

4. **Risk-checkpoint scope-matching uses glob patterns** (`fnmatch`,
   e.g. `pkg_a/**`) against the plan artifact's `declared_scope.in_scope`
   list. §9.5 says "an explicit list of files/modules/packages" without
   specifying a pattern language; glob was chosen as the simplest
   reasonable interpretation. If D2's plans use exact file lists instead
   of globs, `fnmatch` still works (a literal path matches itself), so
   this should be forward-compatible either way.

## Acceptance criteria — checklist (from the brief)

- [x] **A test suite pass alone does not mark a change eligible for
      human review without layers 5-7 also completing.**
      `tests/test_verdict_not_sufficient.py` (synthetic layer results) and
      `tests/test_pipeline_end_to_end.py::test_clean_1_through_4_but_layer_7_checkpoint_is_not_eligible`
      (real layers 1-4 green, real layer 7 checkpoint) both prove this.
- [x] **A diff that renames a symbol with an unreferenced caller outside
      the plan's declared scope trips the risk checkpoint — verified with
      a fixture.** `tests/test_layer7_completion_check.py::test_rename_with_unopened_caller_outside_declared_scope_trips_checkpoint`,
      against D3's real index server and its real multi-package fixture.
- [x] **The isolated-context reviewer runs in a genuinely separate
      context from the implementer (no shared conversation history).**
      `tests/test_layer5_isolated_review.py` proves this both structurally
      (`inspect.signature`) and at runtime (mutate-after-build).
- [x] **A flaky test is distinguished from a genuine regression in the
      report.** `tests/test_flaky_detection.py`, against a real
      fixed-schedule flaky fixture and a real always-failing fixture in
      the same run.
- [x] **Retry budget exhaustion escalates with full diagnostics, doesn't
      silently retry again.** `tests/test_retry_budget.py` proves both the
      "retry consumed, loops back" and "budget exhausted, escalates" paths,
      and that `attempts_used` never exceeds `max_attempts`.

Every layer (1-7) additionally has its own dedicated test module proving
its specific brief requirement (real linter catching a real violation,
real security scanner catching a real finding, real regression detection,
etc.) — see the table above and each `tests/test_layerN_*.py` module's
docstring for the exact acceptance-criterion text it targets.

## Explicitly out of scope (per the brief)

- The human change-review gate itself (D9) — this produces the report D9
  presents.
- CI execution infrastructure — this orchestrates/interprets F3's CI
  contract, it doesn't replace the org's CI.
