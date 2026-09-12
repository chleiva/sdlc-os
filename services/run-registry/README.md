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

## Install & run tests

```bash
cd services/run-registry
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/python -m pytest -v
```

33 tests, no external services required (SQLite-backed).

## Consumed by

Nearly everything else in `services/` — always via `RegistryService`,
imported as a local editable dependency (`pip install -e ../run-registry`
from a consuming service), never by reaching into its SQLite file
directly.
