""""Is this due" scheduling against a stored last-run timestamp.

Both the benchmark suite runner (Section 20.1: "run quarterly + on-demand
for the Phase 0 smoke test") and the self-hosted-vs-frontier comparison
(Section 20.1's Rev 3 bullet: "every quarter") share the same shape: a
named schedule, a cadence, and a real "is this due" function checked
against a persisted last-run timestamp -- not just documentation of
intent. `DueScheduler` is that one real implementation; both consumers
key their own schedule by name (e.g. a suite name, or
"self-hosted-vs-frontier") so they don't collide in the same store.

On-demand runs (Section 20.1's Phase 0 smoke-test subset, or a human
forcing a suite off-cadence) call `run` with `force=True`, which bypasses
`is_due` entirely but still records the run via `mark_run` -- an
on-demand run resets the cadence clock exactly as a scheduled one would.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from evaluation_harness.timeutil import parse_iso, to_iso, utcnow

DEFAULT_CADENCE = timedelta(days=90)  # "quarterly"


class LastRunStore(Protocol):
    """Where a schedule's last-run timestamp is persisted."""

    def get_last_run(self, key: str) -> datetime | None: ...

    def set_last_run(self, key: str, when: datetime) -> None: ...


class InMemoryLastRunStore:
    """Process-local last-run store -- the default, and what every test
    in this package uses directly (no filesystem dependency needed)."""

    def __init__(self) -> None:
        self._runs: dict[str, datetime] = {}

    def get_last_run(self, key: str) -> datetime | None:
        return self._runs.get(key)

    def set_last_run(self, key: str, when: datetime) -> None:
        self._runs[key] = when


class JSONFileLastRunStore:
    """A durable last-run store backed by a single JSON file -- so a
    real deployment's schedule survives a process restart without
    standing up a new database. Self-contained: one file, one dict of
    `{key: iso_timestamp}`.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def _read(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text())

    def get_last_run(self, key: str) -> datetime | None:
        data = self._read()
        raw = data.get(key)
        return parse_iso(raw) if raw else None

    def set_last_run(self, key: str, when: datetime) -> None:
        data = self._read()
        data[key] = to_iso(when)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2, sort_keys=True))


@dataclass
class DueScheduler:
    """Real "is this due" logic against a stored last-run timestamp.

    A schedule with no recorded last run is always due (a suite that has
    never run is due immediately, not three months from now).
    """

    store: LastRunStore
    cadence: timedelta = DEFAULT_CADENCE

    def is_due(self, key: str, *, now: datetime | None = None) -> bool:
        now = now or utcnow()
        last_run = self.store.get_last_run(key)
        if last_run is None:
            return True
        return now - last_run >= self.cadence

    def mark_run(self, key: str, *, now: datetime | None = None) -> None:
        self.store.set_last_run(key, now or utcnow())

    def next_due_at(self, key: str) -> datetime | None:
        last_run = self.store.get_last_run(key)
        if last_run is None:
            return None
        return last_run + self.cadence
