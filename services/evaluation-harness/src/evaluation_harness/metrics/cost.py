"""Cost per successfully completed task (Section 20.2 bullet 5, Section
16.2), including discarded/retried work.

`AttemptCostSource` is the pluggable seam: `run_registry`'s `Attempt`
model carries no cost field (cost is metered by whatever actually runs
the work -- Section 16.2's per-agent-identity/per-tenant GPU-hour and
token metering, which is D6/observability territory, not F2's schema).
`SyntheticAttemptCostSource` is this environment's deterministic stand-in
so the aggregation pipeline below can be proven real; a production
integration swaps it for one reading real per-attempt cost off the
observability/billing pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from run_registry import RegistryService, stages
from run_registry.models import Attempt

from evaluation_harness.timeutil import parse_iso, utcnow


@runtime_checkable
class AttemptCostSource(Protocol):
    def cost_for_attempt(self, attempt: Attempt) -> float: ...


class SyntheticAttemptCostSource:
    """Deterministic per-attempt dollar cost: a fixed base cost plus a
    duration-proportional component, with a multiplier for non-initial
    attempts (a retry/re-plan/resume is real re-spent compute on top of
    whatever the prior attempt already spent -- Section 16.2's
    "discarded work ... tracked as its own cost line").
    """

    def __init__(
        self,
        *,
        base_cost: float = 1.50,
        hourly_rate: float = 4.00,  # mirrors Section 13.3's on-demand g7e.2xlarge order of magnitude
        retry_multiplier: float = 1.25,
    ):
        self._base_cost = base_cost
        self._hourly_rate = hourly_rate
        self._retry_multiplier = retry_multiplier

    def cost_for_attempt(self, attempt: Attempt) -> float:
        start = parse_iso(attempt.start_ts)
        end = parse_iso(attempt.end_ts) if attempt.end_ts else utcnow()
        hours = max((end - start).total_seconds() / 3600.0, 0.01)
        cost = self._base_cost + hours * self._hourly_rate
        if attempt.reason != "initial":
            cost *= self._retry_multiplier
        return round(cost, 4)


@dataclass(frozen=True)
class CostReport:
    tenant_id: str
    completed_task_count: int
    total_cost: float
    completed_task_cost: float  # cost of the final, successful attempt on each completed run
    discarded_cost: float  # cost of every other attempt (retries/re-plans/abandoned runs)
    cost_per_completed_task: float | None  # None when no task has completed yet


def compute_cost_per_completed_task(
    *, registry: RegistryService, tenant_id: str, cost_source: AttemptCostSource
) -> CostReport:
    """Cost per successfully completed task, counting ALL attempts across
    ALL runs (not just completed ones) in `total_cost`/`discarded_cost` --
    exactly the "including discarded-work" requirement: a story that was
    retried twice then abandoned still shows up as real spend, it is just
    never divided into a completed-task denominator.
    """
    result = registry.list_runs(tenant_id=tenant_id, limit=10_000)
    runs = result.data if result.is_ok else []

    total_cost = 0.0
    completed_task_cost = 0.0
    discarded_cost = 0.0
    completed_count = 0

    for run in runs:
        attempts_result = registry.list_attempts(tenant_id=tenant_id, run_id=run.id)
        attempts = attempts_result.data if attempts_result.is_ok else []
        if not attempts:
            continue
        ordered = sorted(attempts, key=lambda a: a.attempt_number)
        is_completed = run.stage == stages.COMPLETED
        if is_completed:
            completed_count += 1
        for idx, attempt in enumerate(ordered):
            cost = cost_source.cost_for_attempt(attempt)
            total_cost += cost
            is_final_attempt = idx == len(ordered) - 1
            if is_completed and is_final_attempt:
                completed_task_cost += cost
            else:
                discarded_cost += cost

    # total_cost == completed_task_cost + discarded_cost by construction; dividing
    # the whole system's spend (successes AND thrown-away work) by the number of
    # tasks that actually completed is the "including discarded/retried work"
    # requirement -- a retry-heavy or abandonment-heavy period raises this number
    # even if every *completed* task itself only needed one clean attempt.
    cost_per_completed_task = total_cost / completed_count if completed_count else None

    return CostReport(
        tenant_id=tenant_id,
        completed_task_count=completed_count,
        total_cost=round(total_cost, 4),
        completed_task_cost=round(completed_task_cost, 4),
        discarded_cost=round(discarded_cost, 4),
        cost_per_completed_task=(
            round(cost_per_completed_task, 4) if cost_per_completed_task is not None else None
        ),
    )
