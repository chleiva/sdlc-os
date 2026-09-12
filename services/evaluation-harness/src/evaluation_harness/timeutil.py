"""Shared ISO-8601 timestamp helpers.

`run_registry.repository.now_iso()` writes timestamps as
`datetime.now(timezone.utc).isoformat()`. Every timestamp this package
reads back from `RegistryService` (`Attempt.start_ts`/`end_ts`,
`Run.created_at`/`updated_at`) is in that same format, so this module is
the one place that knows how to parse it back into an aware `datetime`.
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def hours_between(start_iso: str, end_iso: str) -> float:
    return (parse_iso(end_iso) - parse_iso(start_iso)).total_seconds() / 3600.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
