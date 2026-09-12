"""Quarterly self-hosted-vs-frontier-API comparison (Section 13.1,
Section 13.4, Section 20.1's Rev 3 bullet): "A comparison run of the
self-hosted model against the frontier-API alternative on the same
internal suite, every quarter, so the quality trade-off ... is a tracked
number, not a one-time assumption."

`SelfHostedVsFrontierComparison` reuses the same `DueScheduler` "is this
due" pattern as the benchmark runner, runs the *same* task set through
two `BenchmarkSuite` variants (standing in for two model backends), and
appends one `ComparisonTrendPoint` per run to a `TrendStore` -- so this
is a genuine trend line over multiple recorded runs, queryable in order,
not a single number recomputed in place each time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from evaluation_harness.benchmarks.base import BenchmarkResult, BenchmarkTask
from evaluation_harness.scheduling import DueScheduler
from evaluation_harness.timeutil import to_iso, utcnow

DEFAULT_SCHEDULE_KEY = "self-hosted-vs-frontier"


class ModelBackendSuite:
    """A `BenchmarkSuite` standing in for "the same internal suite, run
    against model backend X". Two instances sharing `base_tasks` but
    different `name`/`pass_bias` model two backends (e.g. the self-hosted
    Ornith endpoint vs. a frontier-API model, Section 13) evaluated on
    identical tasks -- exactly what Section 20.1's Rev 3 bullet asks for.

    `pass_bias` is the deterministic fraction of the task set this
    backend clears -- higher = a stronger backend. Grading is
    rank-based, not hash-based: every task has a fixed, stable
    "normalized difficulty" from its position in `base_tasks` (the same
    position regardless of which backend runs it), and a backend passes
    exactly its easiest `pass_bias` fraction of tasks. This is what
    makes two backends' scores on "the same internal suite" directly,
    monotonically comparable -- and what makes a multi-quarter trend
    (Section 20.1's Rev 3 bullet) move predictably as `pass_bias`
    changes release to release, rather than jittering on suite-name
    hashing noise.
    """

    def __init__(self, *, name: str, base_tasks: Sequence[BenchmarkTask], pass_bias: float):
        if not (0.0 <= pass_bias <= 1.0):
            raise ValueError("pass_bias must be in [0, 1]")
        self.name = name
        self._tasks = tuple(base_tasks)
        self._pass_bias = pass_bias
        self._difficulty_by_task_id = {
            t.task_id: (idx + 0.5) / len(self._tasks) for idx, t in enumerate(self._tasks)
        }

    def tasks(self) -> Sequence[BenchmarkTask]:
        return self._tasks

    def run(self, task: BenchmarkTask) -> BenchmarkResult:
        normalized_difficulty = self._difficulty_by_task_id[task.task_id]
        passed = normalized_difficulty <= self._pass_bias
        return BenchmarkResult(
            task_id=task.task_id,
            family=task.family,
            passed=passed,
            score=round(1.0 - normalized_difficulty, 4),
            duration_s=20.0 if task.difficulty == "short" else 140.0,
            details={"difficulty": task.difficulty, "backend": self.name},
        )


def pass_rate(results: Sequence[BenchmarkResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.passed) / len(results)


@dataclass(frozen=True)
class ComparisonTrendPoint:
    recorded_at: str  # ISO-8601
    self_hosted_pass_rate: float
    frontier_pass_rate: float
    delta: float  # frontier - self_hosted; positive means frontier still ahead


class TrendStore:
    """Append-only trend-point store, queryable in recorded order."""

    def __init__(self) -> None:
        self._points: list[ComparisonTrendPoint] = []

    def append(self, point: ComparisonTrendPoint) -> None:
        self._points.append(point)

    def all_in_order(self) -> list[ComparisonTrendPoint]:
        return sorted(self._points, key=lambda p: p.recorded_at)

    def __len__(self) -> int:
        return len(self._points)


class SelfHostedVsFrontierComparison:
    def __init__(
        self,
        *,
        scheduler: DueScheduler,
        trend_store: TrendStore,
        schedule_key: str = DEFAULT_SCHEDULE_KEY,
    ):
        self._scheduler = scheduler
        self._trend_store = trend_store
        self._schedule_key = schedule_key

    def run(
        self,
        *,
        self_hosted_suite: ModelBackendSuite,
        frontier_suite: ModelBackendSuite,
        force: bool = False,
        now: datetime | None = None,
    ) -> ComparisonTrendPoint | None:
        """Run both backends on the same task set and record one trend
        point. Returns `None` (no point recorded) when the comparison is
        not due and `force` was not set -- callers must not mistake a
        skip for a zero-delta result.
        """
        now = now or utcnow()
        if not force and not self._scheduler.is_due(self._schedule_key, now=now):
            return None

        self_hosted_results = [self_hosted_suite.run(t) for t in self_hosted_suite.tasks()]
        frontier_results = [frontier_suite.run(t) for t in frontier_suite.tasks()]
        self_hosted_rate = pass_rate(self_hosted_results)
        frontier_rate = pass_rate(frontier_results)

        point = ComparisonTrendPoint(
            recorded_at=to_iso(now),
            self_hosted_pass_rate=self_hosted_rate,
            frontier_pass_rate=frontier_rate,
            delta=round(frontier_rate - self_hosted_rate, 6),
        )
        self._trend_store.append(point)
        self._scheduler.mark_run(self._schedule_key, now=now)
        return point
