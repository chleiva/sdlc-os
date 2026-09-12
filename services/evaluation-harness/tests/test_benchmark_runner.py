"""Real orchestration over registered `BenchmarkSuite`s (Section 20.1):
runs each suite's fixture tasks, aggregates pass/fail + timing, and gates
execution on `DueScheduler`'s "is this due" check unless forced.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from evaluation_harness.benchmarks import (
    AiderPolyglotStyleSuite,
    GaiaStyleSuite,
    SweBenchStyleSuite,
    TerminalBenchStyleSuite,
)
from evaluation_harness.runner import BenchmarkRunner
from evaluation_harness.scheduling import DueScheduler, InMemoryLastRunStore


@pytest.fixture
def runner():
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    r = BenchmarkRunner(scheduler)
    r.register(SweBenchStyleSuite())
    r.register(TerminalBenchStyleSuite())
    r.register(AiderPolyglotStyleSuite())
    r.register(GaiaStyleSuite())
    return r


def test_all_four_named_families_are_registered(runner):
    assert runner.registered_suite_names() == sorted(
        ["swebench-style", "terminal-bench-style", "aider-polyglot-style", "gaia-style"]
    )


def test_forced_run_executes_and_aggregates_pass_fail_and_timing(runner):
    outcome = runner.run_suite("swebench-style", force=True)
    assert outcome.ran is True
    assert outcome.reason == "forced"
    result = outcome.result
    assert result is not None
    assert result.total == 6  # 3 short + 3 long_horizon fixture tasks
    # Real aggregation, not a suite that trivially always passes: some
    # tasks pass and some fail, proving the runner distinguishes them.
    assert 0 < result.passed_count < result.total
    assert result.total_duration_s > 0


def test_swebench_style_suite_short_tasks_pass_far_more_than_long_horizon(runner):
    """Mirrors Section 11.2's finding: short tasks clear a low bar easily;
    long-horizon tasks collapse. Not asserting exact spec percentages
    (this is a fixture, not the real benchmark) -- asserting the fixture
    reproduces the *shape* of the finding the runner exists to surface.
    """
    outcome = runner.run_suite("swebench-style", force=True)
    result = outcome.result
    short_results = result.by_difficulty("short")
    long_results = result.by_difficulty("long_horizon")
    assert len(short_results) == 3
    assert len(long_results) == 3
    short_pass_rate = sum(1 for r in short_results if r.passed) / len(short_results)
    long_pass_rate = sum(1 for r in long_results if r.passed) / len(long_results)
    assert short_pass_rate > long_pass_rate


def test_suite_not_due_is_skipped_not_silently_run(runner):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = runner.run_suite("terminal-bench-style", force=False, now=t0)
    assert first.ran is True
    assert first.reason == "due"

    second = runner.run_suite("terminal-bench-style", force=False, now=t0 + timedelta(days=1))
    assert second.ran is False
    assert second.reason == "not-due"
    assert second.result is None


def test_on_demand_smoke_run_uses_a_fixed_task_subset():
    """F1's step-5 smoke-run hook consumes a fixed subset of the suite
    (per the brief's "Interfaces you consume"). `task_ids` is that
    subset-selection mechanism, and it works even when the suite is not
    otherwise due, because `force=True` is exactly the on-demand path.
    """
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    runner = BenchmarkRunner(scheduler)
    runner.register(TerminalBenchStyleSuite())

    outcome = runner.run_suite(
        "terminal-bench-style", force=True, task_ids=["term-1", "term-2"]
    )
    assert outcome.ran is True
    assert outcome.result.total == 2
    assert {r.task_id for r in outcome.result.results} == {"term-1", "term-2"}


def test_run_all_due_runs_every_registered_suite_the_first_time(runner):
    outcomes = runner.run_all_due()
    assert {o.suite_name for o in outcomes} == set(runner.registered_suite_names())
    assert all(o.ran for o in outcomes)


def test_run_all_due_then_skips_everything_immediately_after(runner):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    runner.run_all_due(now=t0)
    second_pass = runner.run_all_due(now=t0 + timedelta(hours=1))
    assert all(not o.ran for o in second_pass)
