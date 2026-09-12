"""Benchmark suite orchestration (Section 20.1).

`BenchmarkRunner` is real orchestration over the registered
`BenchmarkSuite`s: it runs each suite's fixture tasks, aggregates
pass/fail and timing per suite (`SuiteRunResult`), and gates execution
through `DueScheduler` so a suite only runs when it is quarterly-due --
unless the caller explicitly asks for an on-demand run (Section 20.1:
"run quarterly + on-demand for the Phase 0 smoke test").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from evaluation_harness.benchmarks.base import BenchmarkSuite, SuiteRunResult
from evaluation_harness.scheduling import DueScheduler


@dataclass(frozen=True)
class SuiteRunOutcome:
    suite_name: str
    ran: bool  # False when skipped because not due and not forced
    result: SuiteRunResult | None
    reason: str


class BenchmarkRunner:
    def __init__(self, scheduler: DueScheduler):
        self._scheduler = scheduler
        self._suites: dict[str, BenchmarkSuite] = {}

    def register(self, suite: BenchmarkSuite) -> None:
        self._suites[suite.name] = suite

    def registered_suite_names(self) -> list[str]:
        return sorted(self._suites)

    def run_suite(
        self,
        suite_name: str,
        *,
        force: bool = False,
        now: datetime | None = None,
        task_ids: list[str] | None = None,
    ) -> SuiteRunOutcome:
        """Run one registered suite.

        `force=True` is the on-demand path (e.g. F1's step-5 smoke-run
        hook, which per the brief consumes "a fixed subset" of this
        suite -- that subset is `task_ids`). Without `force`, the suite
        only actually runs if `DueScheduler.is_due` says so; otherwise
        it is skipped and reported as such rather than silently no-op'd.
        """
        suite = self._suites[suite_name]
        if not force and not self._scheduler.is_due(suite_name, now=now):
            return SuiteRunOutcome(
                suite_name=suite_name, ran=False, result=None, reason="not-due"
            )

        tasks = suite.tasks()
        if task_ids is not None:
            wanted = set(task_ids)
            tasks = [t for t in tasks if t.task_id in wanted]

        results = tuple(suite.run(task) for task in tasks)
        run_result = SuiteRunResult(suite_name=suite_name, results=results)
        self._scheduler.mark_run(suite_name, now=now)
        return SuiteRunOutcome(
            suite_name=suite_name,
            ran=True,
            result=run_result,
            reason="forced" if force else "due",
        )

    def run_all_due(self, *, now: datetime | None = None) -> list[SuiteRunOutcome]:
        return [
            self.run_suite(name, force=False, now=now)
            for name in self.registered_suite_names()
        ]

    def run_all_forced(self, *, now: datetime | None = None) -> list[SuiteRunOutcome]:
        return [
            self.run_suite(name, force=True, now=now)
            for name in self.registered_suite_names()
        ]
