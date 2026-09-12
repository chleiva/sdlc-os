# D8 — Fleet Control Dashboard

A read-only, Kanban-style view over the Run Registry (F2): one card per
in-flight run, columns mapped to the pipeline stages, live-updating
(poll-based), tenant-scoped. See
`docs/deliverables/wave1-D8-fleet-dashboard.md` for the brief this
implements, and master spec §16.5.

This service **only reads** the Registry, through F2's
`run_registry.RegistryService` public API — it never opens the
Registry's SQLite file directly, and it is not a second place stage
transitions get written to (`tests/test_no_direct_db_access.py` is a
structural/fitness test proving the former).

## Layout

```
services/fleet-dashboard/
  src/fleet_dashboard/
    columns.py            stage -> board-column mapping (brief's table)
    budgets.py             Sec 9.4 reference budget (see "Known gaps" below)
    poll_state.py          ephemeral, in-process poll history (checkpoint
                            counts, verification-bounce-back detection)
    auth.py                 stand-in viewer-identity -> tenant_id map
    cards.py                builds one JSON card per Run
    dashboard_service.py    the only module that calls RegistryService
    http_app.py              stdlib http.server HTTP layer (no framework)
    static/index.html        single-file Kanban UI (vanilla JS, polls
                             /api/cards every few seconds)
    seed_demo.py             DEMO data seeding (not real production data)
    __main__.py              `python -m fleet_dashboard` entry point
  tests/                    pytest suite (see "Tests" below)
```

## Setup

Requires Python >= 3.11. This package depends on `services/run-registry`
as a **local path dependency** (never a copy of its models) — see
`pyproject.toml`. `run-registry` itself resolves its migrations
directory relative to its own installed source location, so it **must**
be installed in *editable* mode, not built into a wheel copy, or its
migrations won't be found (`no such table: runs` is the symptom if this
step is skipped or done in the wrong order):

```bash
cd services/fleet-dashboard
python3 -m venv .venv
.venv/bin/pip install --upgrade pip

# 1. Editable-install the Registry Service first...
.venv/bin/pip install -e ../run-registry

# 2. ...then this package. Its own pyproject.toml also names
#    run-registry as a dependency (a direct `file:../run-registry`
#    reference, per the brief), which makes pip rebuild and reinstall it
#    as a plain (non-editable) copy as a side effect of step 2 -- so
#    reassert the editable install once more, without touching its
#    dependencies, to undo that:
.venv/bin/pip install -e ".[dev]"
.venv/bin/pip install -e ../run-registry --force-reinstall --no-deps
```

Verify the fix took:

```bash
.venv/bin/python -c "
import run_registry._internal.db as db
assert db._MIGRATIONS_DIR.exists(), db._MIGRATIONS_DIR
print('OK:', db._MIGRATIONS_DIR)
"
```

This three-step dance is a real, slightly awkward consequence of
consuming another deliverable's package as an installed dependency
rather than vendoring it; it is called out explicitly in this
deliverable's report as something a human may want F2 to make more
robust (e.g. packaging `migrations/` as package data and looking it up
via `importlib.resources` instead of `__file__`-relative parent
traversal) — not something this deliverable can fix by editing
`services/run-registry` itself.

## Running the tests

```bash
.venv/bin/pytest -q
```

All tests use a real, fresh SQLite-backed `RegistryService` per test
(via `tmp_path`), a real HTTP server on a loopback port for the
HTTP-level tests, and real `time.sleep()`s around the live-update
assertions — nothing here is mocked.

## Running the server

```bash
export RUN_REGISTRY_DB_PATH=/tmp/fleet_dashboard_demo.db   # any scratch path
export FLEET_DASHBOARD_PORT=8808                            # default
.venv/bin/python -m fleet_dashboard
```

Then open <http://127.0.0.1:8808/> in a browser. It starts empty (no
runs yet) until you either point it at a database another component has
been writing to, or seed demo data (below).

### Viewing it as a specific tenant

The dashboard resolves *who's asking* from a viewer token (a stand-in for
real OIDC-based identity, see `src/fleet_dashboard/auth.py`'s docstring)
via an `Authorization: Bearer <token>` header, or a `?token=` query
param (which the static UI's "Viewer token" box uses, since browsers
can't easily set custom headers from a plain page navigation). Demo
tokens (see `auth.py`):

| token           | authorized tenant(s)   | role     |
|-----------------|-------------------------|----------|
| `demo-viewer-a` | `tenant-a`               | viewer   |
| `demo-viewer-b` | `tenant-b`               | viewer   |
| `demo-operator` | `tenant-a`, `tenant-b`   | operator |

Type `demo-viewer-a` into the "Viewer token" box and click Apply, or
curl it directly:

```bash
curl -s -H "Authorization: Bearer demo-viewer-a" http://127.0.0.1:8808/api/cards | python3 -m json.tool
```

### Seeding demo data

D1/D2/D6/D7 (job dispatcher, orchestrator, model serving, verification)
don't exist yet, so there is no real traffic to populate the Registry.
**`seed_demo.py` seeds demo/test data only, directly through
`RegistryService`'s own create-Run / append-Attempt / transition-stage /
write-checkpoint / write-execution-location operations — never a
production data source.** Do not point it at a real deployment's
Registry database.

```bash
# In a second terminal, while the server above is running against the
# SAME db path:
.venv/bin/python -m fleet_dashboard.seed_demo /tmp/fleet_dashboard_demo.db tenant-a
```

This populates one card in every column for `tenant-a`, then (after a
5s pause, so you can watch it happen live on the dashboard) transitions
one run from `verification` back to `implementation` to demonstrate the
"visible edge back to Development on a verification failure" behavior
in real time. Re-run with a second tenant id (e.g. `tenant-b`) and view
it with the `demo-viewer-b` token to see tenant isolation.

## Known gaps / spec ambiguities flagged for human review

These are documented inline at the point they matter (grep `FLAGGED FOR
HUMAN REVIEW` under `src/`), summarized here:

1. **"Elapsed time in current stage vs. budget"**: the Run Registry
   schema (`run_registry.models.Run`) has no story-size field and no
   per-stage-budget field — §9.4's budgets are per whole-run wall-clock,
   by story size, and the size isn't stored on the Run row at all. This
   dashboard shows elapsed-in-stage against the §9.4 **M-size default**
   (2h) applied uniformly to every card, labeled as a fleet-wide
   reference, not a per-run figure. Fix requires either a schema
   addition to F2 (a shared-contract change, flagged rather than made
   unilaterally) or a separate read-only lookup of story sizing.

2. **"Checkpoint count" (Development column)**: the Registry stores only
   the single latest `checkpoint_pointer`, not a count. This dashboard
   approximates a count by observing pointer changes across its own
   polls (`checkpoint_count_observed`), which under-counts checkpoints
   written before this dashboard process started watching a given run.

3. **"A visible edge back to Development on a verification failure"**:
   the Registry Service's public API has no `list_stage_history` op
   (the SQL layer has one, `repository.list_stage_history`, but
   `RegistryService` never wraps it), so this dashboard detects the
   verification→implementation loop-back only by watching `Run.stage`
   change across its own successive polls — same limitation as #2: a
   loop-back that happened before this dashboard's first poll of that
   run isn't visible. Recommend F2 add a thin `list_stage_history`
   (and/or `list_runs`-embedded) read op so this is detectable from cold
   start, not just live.

4. **"Waiting on a human answer" (Analysis) / "who a stalled card is
   waiting on" (Design)**: neither is stored on the Run row (no
   assignee/blocked-on field, no Section 18 stuck-detection integration
   in F2's current API). This dashboard shows the underlying sub-stage
   (`intake` vs `research`, `plan_authoring` vs `plan_approval_gate`)
   as the closest available signal and the elapsed-in-stage clock for
   "how long," but cannot show *who* it's waiting on without a new data
   source neither this deliverable nor F2 currently exposes.

5. **Filter by "team" and "autonomy level"**: the Registry schema has no
   `team` or `autonomy_level` field. `team` is approximated from the
   Jira project-key prefix (`PROJ-123` → `PROJ`), a heuristic, not a
   stored fact. `autonomy_level` has no derivable proxy at all; the
   dashboard accepts the query parameter (so callers don't get a hard
   error) but documents, in the API response's `notes` field, that it is
   currently a no-op.

6. **Editable-install ordering** (see "Setup" above) — a packaging
   robustness issue in how `run_registry._internal.db` locates its
   migrations directory when installed as a dependency rather than run
   from its own repo checkout.

None of these are silent — each is flagged at the exact place it's
approximated, in code comments and in the API response itself where
relevant (`notes`, `elapsed_in_stage_is_exact`, `checkpoint_count_observed`'s
naming, etc.), consistent with this dashboard's staleness-disclosure
obligation not to imply data it doesn't have.
