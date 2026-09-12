"""Section 9.4 default budgets and Section 9.3 checkpoint-trigger
computation, unit-tested directly against `checkpoints.py`."""
from __future__ import annotations

import pytest

from orchestrator.checkpoints import (
    Budget,
    DiffStats,
    XLNotRunnableError,
    check_risk,
    check_size,
    check_stuck,
    check_time_cost,
    evaluate_checkpoints,
    next_size_class,
    resolve_budget,
)


@pytest.mark.parametrize(
    "size,minutes,cost,lines,files",
    [
        ("S", 30, 5, 150, 5),
        ("M", 120, 20, 400, 15),
        ("L", 360, 60, 800, 30),
    ],
)
def test_default_budgets_match_section_9_4_verbatim(size, minutes, cost, lines, files):
    budget = resolve_budget(story_size=size, cross_cutting_or_high_risk=False)
    assert budget.wall_clock_minutes == minutes
    assert budget.cost_ceiling_usd == cost
    assert budget.size_checkpoint_lines == lines
    assert budget.size_checkpoint_files == files


def test_cross_cutting_or_high_risk_bumps_to_next_size_class():
    bumped = resolve_budget(story_size="S", cross_cutting_or_high_risk=True)
    assert bumped.story_size == "M"
    bumped_2 = resolve_budget(story_size="M", cross_cutting_or_high_risk=True)
    assert bumped_2.story_size == "L"


def test_xl_is_a_decomposition_signal_never_an_executable_budget():
    with pytest.raises(XLNotRunnableError):
        resolve_budget(story_size="XL", cross_cutting_or_high_risk=False)
    with pytest.raises(XLNotRunnableError):
        resolve_budget(story_size="L", cross_cutting_or_high_risk=True)  # bumps L -> XL


def test_next_size_class_caps_at_xl():
    assert next_size_class("S") == "M"
    assert next_size_class("M") == "L"
    assert next_size_class("L") == "XL"
    assert next_size_class("XL") == "XL"


def test_size_checkpoint_triggers_over_either_threshold():
    budget = Budget("S", 30, 5, 150, 5)
    assert check_size(DiffStats(files_touched=("a", "b"), lines_changed=200), budget) is not None
    assert check_size(DiffStats(files_touched=tuple(f"f{i}" for i in range(6)), lines_changed=10), budget) is not None
    assert check_size(DiffStats(files_touched=("a",), lines_changed=10), budget) is None


def test_risk_checkpoint_is_a_pure_set_difference():
    trigger = check_risk(DiffStats(files_touched=("a.py", "b.py", "c.py"), lines_changed=1), declared_scope_in=("a.py", "b.py"))
    assert trigger is not None
    assert trigger.details["out_of_scope_files"] == ["c.py"]
    assert check_risk(DiffStats(files_touched=("a.py",), lines_changed=1), declared_scope_in=("a.py", "b.py")) is None


def test_time_cost_checkpoint_fires_at_80_percent_of_either_dimension():
    budget = Budget("S", 30, 5, 150, 5)
    assert check_time_cost(elapsed_minutes=24, spend_usd=0, budget=budget) is not None  # 24/30 = 80%
    assert check_time_cost(elapsed_minutes=23.9, spend_usd=0, budget=budget) is None
    assert check_time_cost(elapsed_minutes=0, spend_usd=4, budget=budget) is not None  # 4/5 = 80%


def test_time_cost_hard_stop_flag_at_100_percent():
    budget = Budget("S", 30, 5, 150, 5)
    trigger = check_time_cost(elapsed_minutes=30, spend_usd=0, budget=budget)
    assert trigger.details["hard_stop"] is True
    trigger_warn_only = check_time_cost(elapsed_minutes=25, spend_usd=0, budget=budget)
    assert trigger_warn_only.details["hard_stop"] is False


def test_stuck_checkpoint_default_retry_budget_is_three():
    assert check_stuck(consecutive_same_stage_failures=2) is None
    assert check_stuck(consecutive_same_stage_failures=3) is not None


def test_evaluate_checkpoints_returns_every_triggered_kind():
    budget = Budget("S", 30, 5, 150, 5)
    triggers = evaluate_checkpoints(
        diff_stats=DiffStats(files_touched=("a.py", "z.py"), lines_changed=999),
        declared_scope_in=("a.py",),
        budget=budget,
        elapsed_minutes=100,
        spend_usd=100,
        consecutive_same_stage_failures=5,
    )
    kinds = {t.kind for t in triggers}
    assert kinds == {"size", "risk", "time_cost", "stuck"}
