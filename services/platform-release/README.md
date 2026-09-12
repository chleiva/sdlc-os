# platform-release — D12: Platform Release Engineering

Wave 3 deliverable (master spec §14.15). How the System's *own*
codebase — orchestrator, dispatcher, Registry Service, dashboard, MCP
servers, F1's infra modules — is built, tested, and released. This is
**not** the nine-stage pipeline (§5) the System runs to ship a change to
a *target* repository; that is the product. This is the ordinary
engineering practice for the platform's own control-plane code, and it
mirrors §13.5's model-version lifecycle deliberately (pin, validate,
promote, roll back via `tofu apply`, audit every step) rather than
inventing a second discipline.

Four pieces, each independently real and independently tested:

- **`src/platform_release/contract_ci.py`** — ordinary platform CI: an
  orchestrator that discovers each already-implemented service's own
  test suite and shells out to *that service's own* `pytest` (via its
  own `.venv`), aggregating pass/fail into one report. Reimplements
  nothing any service's own tests already check.
- **`src/platform_release/canary.py`** — `CanaryRouter`: deterministic
  hash-based bucketing of a trigger/tenant/run identifier into "old
  version" vs "new version" at a configurable fraction.
- **`src/platform_release/rollback.py`** + **`infra/`** — a real,
  versioned local OpenTofu composition (the `hashicorp/local` provider
  standing in for a real cloud resource — see below) representing "the
  platform's own deployed version." `apply_platform_version` /
  `release` / `rollback_to_previous` drive real `tofu apply` subprocess
  runs against it.
- **`src/platform_release/module_versioning.py`** — the infra-module
  versioning gate: `validate_module_version` runs a real `tofu init` +
  `validate` + `plan` against a throwaway non-production composition
  wrapping a candidate module version; `promote_module_version` only
  writes a pinned-version record on real success; `validate_cell_definition`
  refuses a tenant cell definition that names any version not already
  promoted.
- **`src/platform_release/audit.py`** — this deliverable's own
  append-only, JSON-lines-backed audit log (the same pattern
  `services/source-control/src/source_control/audit.py` and
  `services/gates/src/gates/audit.py` each independently establish for
  their own domains — a fresh instance here, not an import of either).
- **`src/platform_release/release_manager.py`** — ties the four pieces
  above together into `ReleaseManager.start_release` /
  `.configure_canary` / `.promote_full_rollout` / `.rollback`, so every
  release and rollback action audits itself without each call site
  having to remember to.

## What's real vs. a local stand-in, and why

Same hard constraint as F1/D6: **no live cloud account exists in this
build environment.** Unlike D6 (which had genuinely nothing it could
execute against), this deliverable's own instructions call for making
the rollback/versioning mechanics **genuinely executable** without cloud
credentials:

| Piece | Real | Local stand-in |
|---|---|---|
| `contract_ci.py` orchestrating other services' own pytest | Yes, fully — a real `subprocess` call into each service's real `.venv`/`pytest`; `test_contract_ci.py` runs it against the real `services/mcp-stubs` suite | n/a |
| `CanaryRouter` bucketing | Yes, fully — real `sha256`-based hashing, tested against 2,000–5,000 real synthetic UUIDs per test | n/a |
| `AuditLog` | Yes, fully — real JSON-lines file, durable across reload | n/a |
| Module-versioning gate's `tofu validate`/`plan` | Yes, fully — a real `tofu` subprocess against a real (if minimal) HCL module | n/a |
| "The platform's own deployed version" resource (`infra/modules/platform-release`) | The `tofu apply`/state/rollback *mechanics* are 100% real — real OpenTofu, real state, real subprocess, real on-disk artifact | The resource itself is `hashicorp/local`'s `local_file`, standing in for what a real cloud deployment would use instead (a Helm release version on `infra/modules/orchestrator`'s Deployment, a container image tag, a fleet-wide version-pin ConfigMap). The versioning/rollback logic this proves — `tofu apply` against a prior pinned config/state, no manual undo — is identical either way; only the resource *type* changes. See `infra/modules/platform-release/README.md`. |

Why `local` and not a mock: a mock would only prove that *this
deliverable's own code* calls the right functions in the right order. A
real `tofu apply`/rollback against `local_file` proves the actual
OpenTofu *mechanics* — state read-back, plan diffing against a changed
variable, an in-place update, a second apply reverting it — genuinely
work, which a Python-level mock of "tofu" could never prove. Swapping
`local_file` for a cloud resource in the module is a resource-type edit,
not a rewrite of the Python orchestration logic above it.

## Interfaces consumed

- F1's IaC engine/module tree (`infra/`) — read for pattern (module
  layout, `tofu`-not-`terraform`, local-backend convention), not
  modified; this deliverable's own `infra/` is new, self-contained, and
  does not call into F1's real cloud modules (those need AWS/GCP/network
  access this deliverable doesn't add).
- D6's per-tenant composition pattern (`services/tenant-cell/infra/`) —
  read for pattern (module-calling-module composition layout); not
  imported or modified.
- F2's Run Registry (`services/run-registry/`) — read for pattern
  (`RegistryService`'s public API, `Run` schema). **Flagged assumption**
  (same one `services/gates/src/gates/audit.py` already flags for its
  own domain): F2's `Run` schema has no `platform_version` column and no
  audit-event table — a platform release spans every tenant's runs, not
  one Run row, so it has no natural home in F2's schema as it stands
  today. This deliverable's `ReleaseHistory`/`AuditLog`/
  `ModuleVersionRegistry` each own their own durable JSON-file store
  instead (same choice `gates/audit.py` made). A human should confirm
  this is the intended home for platform-wide release state, not a
  decision this deliverable is authorized to make unilaterally. No code
  in this package imports `run_registry` — nothing here calls F2's real
  API — since building that concretely would mean inventing a schema
  change F2 doesn't have, which this deliverable's own ground rules (see
  repo root CLAUDE.md) says not to do unilaterally.

## Running the tests

```
cd services/platform-release
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
```

37 tests, all passing (verified from a clean `.venv` reinstall). Needs a
real `tofu` binary on `PATH` (OpenTofu, not the Terraform CLI — same
convention as F1/D6) and network access once, to fetch the
`hashicorp/local` provider (a few hundred KB; cached afterward — see
`tests/conftest.py`'s `TF_PLUGIN_CACHE_DIR` fixture). No AWS/GCP/Azure
account, credentials, or reachable cluster of any kind.

Breakdown:

- `test_audit.py` (5), `test_canary.py` (8) — pure-Python, no subprocess.
- `test_contract_ci.py` (6) — five against `tests/fixtures/fake_services/`
  (one deliberately-passing, one deliberately-failing fake service, each
  with its own real `.venv` symlinked to this package's own, since all
  they need is `pytest` itself); one (`test_orchestrates_a_real_...`)
  against the real, already-installed `services/mcp-stubs` suite —
  skipped automatically if `services/` isn't present in the checkout.
- `test_module_versioning.py` (8) — real `tofu init`/`validate`/`plan`
  subprocess runs against `tests/fixtures/candidate_modules/{v1_valid,v2_invalid}`.
- `test_rollback.py` (4) — real `tofu init`/`apply` subprocess runs
  (twice, in sequence, against the same state) in a throwaway copy of
  `infra/` (see `tests/conftest.py`'s `infra_tree_copy`/
  `platform_deployment_dir` fixtures — never against the tracked
  `infra/` tree itself).
- `test_release_manager.py` (3) — end-to-end: two releases, a canary
  measurement, a full-rollout promotion, and a rollback, through one
  `ReleaseManager`, asserting every step audited itself.

## Validating the IaC directly

```
cd services/platform-release/infra/compositions/platform-deployment
tofu init -input=false
tofu validate
tofu fmt -check -recursive ../..
tofu apply -input=false -auto-approve -var platform_version=v1 -var release_id=demo-1
cat deployed-version.json
tofu apply -input=false -auto-approve -var platform_version=v0 -var release_id=demo-2 -var rolled_back_from=v1
cat deployed-version.json   # now reflects v0 -- the rollback
rm -rf .terraform terraform.tfstate* deployed-version.json   # clean up the manual demo run
```

## What a human still needs to do for a real (non-local) deployment

1. Swap `infra/modules/platform-release`'s `local_file` resource for
   whatever concretely represents "the platform's control-plane
   version" in the target cloud (a Helm release version on F1's real
   `infra/modules/orchestrator`, a container image tag, or a version-pin
   ConfigMap read by the fleet) and point `infra/compositions/platform-deployment/backend.tf`
   at that environment's real remote state backend (S3+DynamoDB, GCS,
   etc.) instead of the local backend used here.
2. Wire `contract_ci.run_platform_ci` into the organization's actual CI
   trigger (a GitHub Actions/GitLab CI job on PR merge to the platform's
   own repo) rather than invoking it by hand or from a test.
3. Decide (see "Flagged assumption" above) whether platform-release
   audit events, canary-routing awareness of which tenant/run is on
   which platform version, and pinned module-version records belong in
   a shared, schema-owned store (extending F2's Run Registry, or a new
   platform-wide schema) rather than this deliverable's own three
   independent JSON-file stores.
4. Point `CanaryRouter`'s routing decision at the real request path (job
   dispatcher's trigger-intake, most likely) so `route()`'s result
   actually selects which orchestrator/dispatcher/Registry-Service
   version handles a given trigger, rather than only being measured in a
   test.
5. Decide who is authorized to call `promote_module_version`/
   `ReleaseManager.rollback` in production (this deliverable enforces
   *that a real validation ran*, not *who* is allowed to trigger one —
   an authorization/RBAC layer per spec §12/§17 is a separate concern).
