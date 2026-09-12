"""Short-task vs. long-horizon classification from real Attempt history.

ASSUMPTION FLAGGED FOR HUMAN REVIEW (same convention as run-registry's
`stages.py`): the master spec names "long-horizon" as "stories that span
multiple sessions or a large multi-file diff" (Section 20.2) but does not
give a precise, machine-checkable threshold. `Run`/`Attempt` (F2's schema)
also carries no explicit story-size/complexity field to key off of. This
module's threshold-based heuristic -- an attempt count at or above
`attempt_count` (multiple attempts is a direct, real signal of "spanned
multiple sessions": a new Attempt is only ever appended on a re-plan,
retry, or resume-after-interruption, per `run_registry.models.Attempt`),
or elapsed wall-clock time at or above `elapsed_hours` -- is this
implementation's reasonable interpretation, not verbatim spec text.
A human should confirm these thresholds (or replace this heuristic with a
real story-size field on `Run` in a future F2 revision) before this
becomes load-bearing for a real autonomy-expansion decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

from run_registry.models import Attempt

from evaluation_harness.timeutil import parse_iso, utcnow

SHORT = "short"
LONG_HORIZON = "long_horizon"


@dataclass(frozen=True)
class HorizonThresholds:
    attempt_count: int = 3
    elapsed_hours: float = 4.0


DEFAULT_THRESHOLDS = HorizonThresholds()


def classify_horizon(
    attempts: Sequence[Attempt],
    *,
    thresholds: HorizonThresholds = DEFAULT_THRESHOLDS,
    now: datetime | None = None,
) -> str:
    """Classify a Run's task horizon from its real Attempt history.

    An empty attempt list (should not happen for a real Run, which
    always has at least its initial Attempt) is treated as `short` --
    fails safe toward the smaller bucket rather than raising.
    """
    if not attempts:
        return SHORT
    ordered = sorted(attempts, key=lambda a: a.attempt_number)
    attempt_count = ordered[-1].attempt_number
    first_start = parse_iso(ordered[0].start_ts)
    last = ordered[-1]
    end = parse_iso(last.end_ts) if last.end_ts else (now or utcnow())
    elapsed_hours = (end - first_start).total_seconds() / 3600.0
    if attempt_count >= thresholds.attempt_count or elapsed_hours >= thresholds.elapsed_hours:
        return LONG_HORIZON
    return SHORT
