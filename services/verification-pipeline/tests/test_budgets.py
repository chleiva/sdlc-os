"""Sec. 9.4's default budgets/thresholds, plus D7's own retry-count
addition (budgets.py's module docstring flags this as an interpretive
decision -- Sec. 9.4 doesn't pin a specific retry-attempt count)."""
import pytest

from verification_pipeline.budgets import (
    BUDGETS_BY_SIZE,
    RetryBudget,
    budget_for,
    checkpoint_threshold,
)


def test_sec_9_4_numbers_are_pinned_verbatim():
    assert BUDGETS_BY_SIZE["S"] == {
        "wall_clock_minutes": 30,
        "cost_ceiling_usd": 5,
        "size_checkpoint_lines": 150,
        "size_checkpoint_files": 5,
    }
    assert BUDGETS_BY_SIZE["M"]["wall_clock_minutes"] == 120
    assert BUDGETS_BY_SIZE["L"]["cost_ceiling_usd"] == 60


def test_xl_is_not_an_executable_budget():
    with pytest.raises(ValueError):
        budget_for("XL", "low")


def test_high_risk_bumps_to_next_size_class_up():
    assert budget_for("S", "high") == BUDGETS_BY_SIZE["M"]
    assert budget_for("M", "cross-cutting") == BUDGETS_BY_SIZE["L"]
    assert budget_for("S", "low") == BUDGETS_BY_SIZE["S"]


def test_checkpoint_fires_at_80_percent():
    assert checkpoint_threshold(100) == 80


def test_retry_budget_consume_and_exhaustion():
    budget = RetryBudget.for_story_size("S")
    assert budget.max_attempts == 2
    assert not budget.exhausted
    budget.consume()
    assert budget.attempts_used == 1
    assert not budget.exhausted
    budget.consume()
    assert budget.exhausted
    with pytest.raises(ValueError):
        budget.consume()
