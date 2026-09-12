"""Quarterly self-hosted-vs-frontier comparison (Section 13.1/13.4,
Section 20.1's Rev 3 bullet): schedulable, and produces a trackable
trend across multiple recorded runs -- not a one-off number.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evaluation_harness.benchmarks.fixtures import SweBenchStyleSuite
from evaluation_harness.comparison import ModelBackendSuite, SelfHostedVsFrontierComparison, TrendStore
from evaluation_harness.scheduling import DueScheduler, InMemoryLastRunStore

BASE_TASKS = SweBenchStyleSuite().tasks()


def _backends(*, self_hosted_bias: float, frontier_bias: float, quarter: str):
    self_hosted = ModelBackendSuite(
        name=f"self-hosted-{quarter}", base_tasks=BASE_TASKS, pass_bias=self_hosted_bias
    )
    frontier = ModelBackendSuite(
        name=f"frontier-{quarter}", base_tasks=BASE_TASKS, pass_bias=frontier_bias
    )
    return self_hosted, frontier


def test_comparison_is_schedulable_quarterly():
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    trend_store = TrendStore()
    comparison = SelfHostedVsFrontierComparison(scheduler=scheduler, trend_store=trend_store)

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    self_hosted, frontier = _backends(self_hosted_bias=0.6, frontier_bias=0.8, quarter="q1")
    point = comparison.run(self_hosted_suite=self_hosted, frontier_suite=frontier, now=t0)
    assert point is not None

    # Not due yet a week later -- skipped, not silently re-recorded.
    self_hosted_2, frontier_2 = _backends(self_hosted_bias=0.6, frontier_bias=0.8, quarter="q1b")
    skipped = comparison.run(
        self_hosted_suite=self_hosted_2, frontier_suite=frontier_2, now=t0 + timedelta(days=7)
    )
    assert skipped is None
    assert len(trend_store) == 1


def test_multiple_quarterly_runs_produce_a_genuine_queryable_trend():
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    trend_store = TrendStore()
    comparison = SelfHostedVsFrontierComparison(scheduler=scheduler, trend_store=trend_store)

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Three quarters, self-hosted bias improving each time (Section 13.4 /
    # 23.4's "promoting an improving open-weight model into a larger role
    # over time") -- frontier held roughly constant.
    quarters = [
        ("q1", 0.40, 0.80, t0),
        ("q2", 0.55, 0.80, t0 + timedelta(days=91)),
        ("q3", 0.70, 0.80, t0 + timedelta(days=182)),
    ]
    for label, self_hosted_bias, frontier_bias, when in quarters:
        self_hosted, frontier = _backends(
            self_hosted_bias=self_hosted_bias, frontier_bias=frontier_bias, quarter=label
        )
        point = comparison.run(
            self_hosted_suite=self_hosted, frontier_suite=frontier, now=when, force=True
        )
        assert point is not None

    ordered = trend_store.all_in_order()
    assert len(ordered) == 3
    # Queryable in order: recorded_at strictly increasing.
    recorded_ats = [p.recorded_at for p in ordered]
    assert recorded_ats == sorted(recorded_ats)
    # A genuine trend, not three identical numbers: the self-hosted delta
    # (frontier - self_hosted) shrinks as the self-hosted model improves.
    deltas = [p.delta for p in ordered]
    assert deltas[0] > deltas[1] > deltas[2]


def test_forced_on_demand_run_bypasses_the_schedule_but_still_records_it():
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    trend_store = TrendStore()
    comparison = SelfHostedVsFrontierComparison(scheduler=scheduler, trend_store=trend_store)

    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    self_hosted, frontier = _backends(self_hosted_bias=0.5, frontier_bias=0.8, quarter="q1")
    comparison.run(self_hosted_suite=self_hosted, frontier_suite=frontier, now=t0)

    self_hosted_2, frontier_2 = _backends(self_hosted_bias=0.5, frontier_bias=0.8, quarter="q1-force")
    forced_point = comparison.run(
        self_hosted_suite=self_hosted_2,
        frontier_suite=frontier_2,
        now=t0 + timedelta(days=1),
        force=True,
    )
    assert forced_point is not None
    assert len(trend_store) == 2
