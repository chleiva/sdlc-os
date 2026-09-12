"""Rework rate (Section 20.2 bullet 3): the proportion of merged/completed
changes that needed a follow-up fix within a defined window.

Real-data modeling choice: `run_registry`'s `Run` has no "fixes/relates
to" link field, so this module detects rework as a real Run being
reopened for the SAME `jira_key` in the SAME `repo` -- a second Run
created within `window_hours` of the first Run's completion. This is a
genuine, literal re-reading of "the same ticket needed a follow-up run",
built entirely from real `Run.jira_key`/`Run.repo`/`Run.created_at` and
real `Attempt.end_ts` (for the completion timestamp) -- no invented
fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from run_registry import RegistryService, stages

from evaluation_harness.timeutil import parse_iso

DEFAULT_WINDOW_HOURS = 24 * 14  # two weeks


@dataclass(frozen=True)
class ReworkReport:
    tenant_id: str
    window_hours: float
    completed_count: int
    rework_count: int
    rework_rate: float
    rework_run_ids: frozenset[str]


def compute_rework_rate(
    *, registry: RegistryService, tenant_id: str, window_hours: float = DEFAULT_WINDOW_HOURS
) -> ReworkReport:
    result = registry.list_runs(tenant_id=tenant_id, limit=10_000)
    runs = result.data if result.is_ok else []
    completed = [r for r in runs if r.stage == stages.COMPLETED]

    rework_ids: set[str] = set()
    for run in completed:
        attempts_result = registry.list_attempts(tenant_id=tenant_id, run_id=run.id)
        attempts = attempts_result.data if attempts_result.is_ok else []
        if not attempts:
            continue
        last_attempt = max(attempts, key=lambda a: a.attempt_number)
        if not last_attempt.end_ts:
            continue
        completion_dt = parse_iso(last_attempt.end_ts)
        window_end = completion_dt + timedelta(hours=window_hours)

        for other in runs:
            if other.id == run.id:
                continue
            if other.repo != run.repo or other.jira_key != run.jira_key:
                continue
            other_created = parse_iso(other.created_at)
            if completion_dt <= other_created <= window_end:
                rework_ids.add(run.id)
                break

    completed_count = len(completed)
    return ReworkReport(
        tenant_id=tenant_id,
        window_hours=window_hours,
        completed_count=completed_count,
        rework_count=len(rework_ids),
        rework_rate=(len(rework_ids) / completed_count) if completed_count else 0.0,
        rework_run_ids=frozenset(rework_ids),
    )
