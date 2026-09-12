"""Cycle time: intake to PR-ready, versus an equivalent human baseline
(Section 20.2 bullet 4).

ASSUMPTION FLAGGED FOR HUMAN REVIEW: `RegistryService`'s public API
exposes Run current-state and append-only Attempt boundaries, but not
per-stage `StageHistoryEntry` timestamps (see `gate_events.py`'s module
docstring for the full explanation of that gap). Reaching the
`packaging` stage specifically ("PR-ready") is therefore not
independently reconstructable from a cold read of Attempt history alone
in the common case (a straight-through run stays on one Attempt right up
to completion). This implementation approximates "intake to PR-ready" as
"intake to terminal" -- the first Attempt's `start_ts` to the final
Attempt's `end_ts` (real, always-populated fields once a run finishes) --
which over-counts the packaging/retrospective tail. A human should
confirm this is an acceptable proxy, or that a future F2 revision should
expose stage-entry timestamps publicly for a tighter measurement.

The human baseline is a REQUIRED keyword argument with no default, by
design: Section 20.2 states cycle time is measured "versus an equivalent
human baseline", and a metric silently defaulting that baseline to zero
(or any other assumed number) would make every run look infinitely
faster than a human, defeating the entire point of the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from run_registry import RegistryService, stages

from evaluation_harness.timeutil import parse_iso


@dataclass(frozen=True)
class CycleTimeEntry:
    run_id: str
    jira_key: str
    hours: float


@dataclass(frozen=True)
class CycleTimeReport:
    tenant_id: str
    human_baseline_hours: float
    entries: tuple[CycleTimeEntry, ...]
    mean_system_hours: float | None
    delta_vs_baseline_hours: float | None  # negative = system faster than baseline


def compute_cycle_time(
    *, registry: RegistryService, tenant_id: str, human_baseline_hours: float
) -> CycleTimeReport:
    """`human_baseline_hours` has no default -- passing it is mandatory,
    not merely documented as expected. See module docstring.
    """
    if human_baseline_hours is None:
        raise ValueError(
            "human_baseline_hours is required (Section 20.2: cycle time is measured "
            "against an equivalent human baseline, never silently assumed to be zero)"
        )
    if human_baseline_hours < 0:
        raise ValueError("human_baseline_hours must be >= 0")

    result = registry.list_runs(tenant_id=tenant_id, limit=10_000)
    runs = result.data if result.is_ok else []

    entries: list[CycleTimeEntry] = []
    for run in runs:
        if run.stage != stages.COMPLETED:
            continue
        attempts_result = registry.list_attempts(tenant_id=tenant_id, run_id=run.id)
        attempts = attempts_result.data if attempts_result.is_ok else []
        if not attempts:
            continue
        ordered = sorted(attempts, key=lambda a: a.attempt_number)
        first, last = ordered[0], ordered[-1]
        if not last.end_ts:
            continue
        hours = (parse_iso(last.end_ts) - parse_iso(first.start_ts)).total_seconds() / 3600.0
        entries.append(CycleTimeEntry(run_id=run.id, jira_key=run.jira_key, hours=hours))

    mean_hours = (sum(e.hours for e in entries) / len(entries)) if entries else None
    delta = (mean_hours - human_baseline_hours) if mean_hours is not None else None

    return CycleTimeReport(
        tenant_id=tenant_id,
        human_baseline_hours=human_baseline_hours,
        entries=tuple(entries),
        mean_system_hours=mean_hours,
        delta_vs_baseline_hours=delta,
    )
