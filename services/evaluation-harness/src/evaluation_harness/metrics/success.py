"""Task success rate, with long-horizon tracked separately from
short-task success (Section 20.2 bullets 1 and 7).

`SuccessRateReport.short_task_success_rate` and
`.long_horizon_success_rate` are two distinct fields, computed from two
distinct buckets of real Runs (bucketed by `task_classification`'s real
Attempt-history heuristic). Nothing in this module blends them into the
`overall_success_rate` headline field -- callers that only read
`overall_success_rate` still see a real number, but the long-horizon
collapse Section 11.2/20.1 describe is never hidden inside it.
"""

from __future__ import annotations

from dataclasses import dataclass

from run_registry import RegistryService, stages

from evaluation_harness.task_classification import (
    DEFAULT_THRESHOLDS,
    LONG_HORIZON,
    HorizonThresholds,
    classify_horizon,
)


@dataclass(frozen=True)
class SuccessRateReport:
    tenant_id: str
    overall_success_rate: float
    overall_count: int
    short_task_success_rate: float | None
    short_task_count: int
    long_horizon_success_rate: float | None
    long_horizon_count: int


def compute_task_success_rate(
    *,
    registry: RegistryService,
    tenant_id: str,
    rework_run_ids: frozenset[str] = frozenset(),
    thresholds: HorizonThresholds = DEFAULT_THRESHOLDS,
) -> SuccessRateReport:
    """Task success rate = acceptance criteria fully met, per task,
    WITHOUT human rework (Section 20.2 bullet 1) -- a run counts as a
    success only if it reached `completed` AND is not in
    `rework_run_ids` (see `metrics.rework.compute_rework_rate`, which
    identifies exactly those runs from real Run data).
    """
    result = registry.list_runs(tenant_id=tenant_id, limit=10_000)
    runs = result.data if result.is_ok else []

    short_total = short_success = 0
    long_total = long_success = 0

    for run in runs:
        attempts_result = registry.list_attempts(tenant_id=tenant_id, run_id=run.id)
        attempts = attempts_result.data if attempts_result.is_ok else []
        horizon = classify_horizon(attempts, thresholds=thresholds)
        is_success = run.stage == stages.COMPLETED and run.id not in rework_run_ids

        if horizon == LONG_HORIZON:
            long_total += 1
            long_success += int(is_success)
        else:
            short_total += 1
            short_success += int(is_success)

    overall_total = short_total + long_total
    overall_success = short_success + long_success

    return SuccessRateReport(
        tenant_id=tenant_id,
        overall_success_rate=(overall_success / overall_total) if overall_total else 0.0,
        overall_count=overall_total,
        short_task_success_rate=(short_success / short_total) if short_total else None,
        short_task_count=short_total,
        long_horizon_success_rate=(long_success / long_total) if long_total else None,
        long_horizon_count=long_total,
    )
