"""Sec. 9.4's default budgets/thresholds, plus D7's own retry-count budget.

Sec. 9.4 pins wall-clock, cost ceiling, and size-checkpoint thresholds per
story size (S/M/L; XL is a decomposition signal, never run directly). It
does NOT pin a specific *retry-attempt count* for the Sec. 9.3 "stuck
checkpoint" ("repeated verification failures on the same issue beyond a
retry budget -> pause and hand back with full diagnostic context") --
that count is left as "configured" the same way every other Sec. 9.3
threshold is. RETRY_ATTEMPTS_BY_SIZE below is D7's own reasonable default
for that missing number, flagged here (and in the top-level README) as an
interpretive decision a human should confirm, exactly the way F3's own
stub README flags its interpretive decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

StorySize = Literal["S", "M", "L", "XL"]

# Sec. 9.4, verbatim numbers.
BUDGETS_BY_SIZE: dict[str, dict[str, float]] = {
    "S": {"wall_clock_minutes": 30, "cost_ceiling_usd": 5, "size_checkpoint_lines": 150, "size_checkpoint_files": 5},
    "M": {"wall_clock_minutes": 120, "cost_ceiling_usd": 20, "size_checkpoint_lines": 400, "size_checkpoint_files": 15},
    "L": {"wall_clock_minutes": 360, "cost_ceiling_usd": 60, "size_checkpoint_lines": 800, "size_checkpoint_files": 30},
}

# Sec. 9.4: "The time/cost checkpoint (Sec. 9.3) fires at 80% of the
# applicable budget, not at 100%."
CHECKPOINT_FRACTION = 0.8

# Sec. 9.4: "A story flagged cross-cutting or high-risk is budgeted at the
# next size class up automatically."
_NEXT_SIZE_UP = {"S": "M", "M": "L", "L": "L"}  # L has no size above it in Sec. 9.4's table.

# --- D7's own addition (NOT in Sec. 9.4 -- see module docstring) ---------
RETRY_ATTEMPTS_BY_SIZE: dict[str, int] = {"S": 2, "M": 3, "L": 4}


def budget_for(story_size: StorySize, risk_tier: str) -> dict[str, float]:
    if story_size == "XL":
        raise ValueError(
            "Sec. 9.4: an XL sizing is a decomposition signal, not an executable budget; "
            "the System requests a split rather than implementing an XL story as one run."
        )
    size = story_size
    if risk_tier in ("high", "cross-cutting"):
        size = _NEXT_SIZE_UP[story_size]
    return dict(BUDGETS_BY_SIZE[size])


def checkpoint_threshold(budget_value: float) -> float:
    return budget_value * CHECKPOINT_FRACTION


@dataclass
class RetryBudget:
    """Bounded retry counter for one verification run (Sec. 9.3's "stuck
    checkpoint" / the D7 brief's "bounded retry budget"). One instance
    lives for the lifetime of one story's verification loop.
    """

    max_attempts: int
    attempts_used: int = 0

    @classmethod
    def for_story_size(cls, story_size: StorySize) -> "RetryBudget":
        if story_size == "XL":
            raise ValueError("XL stories are not run directly (Sec. 9.4); decompose first.")
        return cls(max_attempts=RETRY_ATTEMPTS_BY_SIZE[story_size])

    @property
    def exhausted(self) -> bool:
        return self.attempts_used >= self.max_attempts

    @property
    def remaining(self) -> int:
        return max(0, self.max_attempts - self.attempts_used)

    def consume(self) -> None:
        """Consume one retry attempt. Raises if already exhausted -- a
        caller must check `.exhausted` (or handle the ValueError) rather
        than looping forever; this is what makes "never an infinite loop
        or silent resubmission" (Sec. 11) a property of the code, not a
        convention callers might forget."""
        if self.exhausted:
            raise ValueError("retry budget already exhausted; escalate instead of retrying again")
        self.attempts_used += 1
