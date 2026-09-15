# run-registry — F2: Run Registry Schema + Registry Service

Foundation deliverable (Wave 0). The single authoritative record of every
run's state: the `Run`/`Attempt` schema, and the Registry Service that
fronts it — the one internal API every other component uses to read or
write run state (no component gets a direct database connection to the
underlying store).

Highlights: tenant-scoped fail-closed on every read/write, a fixed
nine-stage-plus-terminal pipeline vocabulary (no free-text status),
append-only Attempt history, and optimistic concurrency via a per-row
version number.

## Layout

```
services/run-registry/
  src/run_registry/
    service.py       RegistryService: the one real internal API every
                      other component uses -- plain, synchronous,
                      tenant-scoped fail-closed on every method
    models.py         the Run/Attempt schema (frozen dataclasses)
    stages.py          the fixed nine-stage-plus-terminal pipeline
                      vocabulary + the legal transition graph
    repository.py       SQLite persistence -- the only module that
                      touches the database directly
    _internal/db.py       connection/schema management, not part of
                      the public surface (the leading underscore is
                      enforced, not just a convention -- see
                      test_no_direct_db_access.py)
    result.py            Result: the ok/empty/error envelope every
                      RegistryService method returns
    errors.py              the named error taxonomy (not-found,
                      permission-denied, illegal-transition,
                      stale-version, invalid-input, ...)
    migrate.py               `python -m run_registry.migrate [db path]`:
                      a thin CLI wrapper for pre-flighting a migration
                      independently of starting the service -- normal
                      startup already applies pending migrations itself
                      (`ConnectionFactory.__init__`), so any consumer
                      (including mcp_server.py below) gets a
                      current-schema DB automatically on first connect
    mcp_server.py              RegistryService exposed as an MCP-shaped
                      server (Section 7.6) via the official Python MCP
                      SDK -- a thin, tool-per-method binding on top of
                      service.py, never a reimplementation of its logic
  migrations/0001_init.sql   the schema's own migration
  tests/                     one pytest module per real concern:
                      tenant scoping, stage transitions, concurrency,
                      attempts, migrations, latency, direct-db-access
                      prevention, and the MCP server itself
```

## Install & run tests

```bash
cd services/run-registry
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m pytest -v
```

33 tests, no external services required (SQLite-backed).

## Run the server

```bash
RUN_REGISTRY_DB_PATH=run_registry.db python -m run_registry.mcp_server   # real MCP server over stdio
```

Applies any pending migration against that DB path automatically on
first connect — no separate migration step needed for a fresh file
(`python -m run_registry.migrate [db path]` exists for pre-flighting a
migration independently, e.g. in CI, before anything else connects).

## Consumed by

Nearly everything else in `services/` — always via `RegistryService`,
imported as a local editable dependency (`pip install -e ../run-registry`
from a consuming service), never by reaching into its SQLite file
directly.
