"""DueScheduler: the real "is this due" primitive (Section 20.1: run
quarterly + on-demand) shared by the benchmark runner and the quarterly
self-hosted-vs-frontier comparison.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from evaluation_harness.scheduling import DueScheduler, InMemoryLastRunStore, JSONFileLastRunStore


def test_never_run_schedule_is_immediately_due():
    scheduler = DueScheduler(store=InMemoryLastRunStore())
    assert scheduler.is_due("swebench-style", now=datetime(2026, 1, 1, tzinfo=timezone.utc)) is True


def test_recently_run_schedule_is_not_due():
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scheduler.mark_run("swebench-style", now=t0)
    assert scheduler.is_due("swebench-style", now=t0 + timedelta(days=1)) is False


def test_schedule_becomes_due_again_after_cadence_elapses():
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scheduler.mark_run("swebench-style", now=t0)
    assert scheduler.is_due("swebench-style", now=t0 + timedelta(days=91)) is True
    assert scheduler.is_due("swebench-style", now=t0 + timedelta(days=89)) is False


def test_two_schedule_keys_are_independent():
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scheduler.mark_run("swebench-style", now=t0)
    assert scheduler.is_due("terminal-bench-style", now=t0 + timedelta(days=1)) is True
    assert scheduler.is_due("swebench-style", now=t0 + timedelta(days=1)) is False


def test_json_file_store_persists_across_scheduler_instances(tmp_path):
    path = tmp_path / "last_run.json"
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

    scheduler_a = DueScheduler(store=JSONFileLastRunStore(path), cadence=timedelta(days=90))
    scheduler_a.mark_run("gaia-style", now=t0)

    # A fresh scheduler instance (simulating a new process) reads the same
    # persisted last-run timestamp back from disk.
    scheduler_b = DueScheduler(store=JSONFileLastRunStore(path), cadence=timedelta(days=90))
    assert scheduler_b.is_due("gaia-style", now=t0 + timedelta(days=1)) is False
    assert scheduler_b.is_due("gaia-style", now=t0 + timedelta(days=91)) is True


def test_next_due_at_is_none_when_never_run():
    scheduler = DueScheduler(store=InMemoryLastRunStore())
    assert scheduler.next_due_at("never-run-suite") is None


def test_next_due_at_reflects_cadence():
    scheduler = DueScheduler(store=InMemoryLastRunStore(), cadence=timedelta(days=90))
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    scheduler.mark_run("swebench-style", now=t0)
    assert scheduler.next_due_at("swebench-style") == t0 + timedelta(days=90)
