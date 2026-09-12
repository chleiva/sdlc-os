"""Budget lookup for the "elapsed time in current stage vs. budget"
card field (brief + master spec Sec. 16.5, citing Sec. 9.4).

ASSUMPTION FLAGGED FOR HUMAN REVIEW (same convention run-registry's own
`stages.py` uses for its own flagged interpretation): Section 9.4 pins a
default *whole-run* wall-clock budget per story size (S=30min, M=2h,
L=6h; XL is not run directly). It is not a per-*stage* budget, and the
Run Registry schema (Section 14.12, `run_registry.models.Run`) has no
story-size field and no per-stage budget field at all -- only
`created_at`/`updated_at` timestamps and the current `stage`. So this
dashboard cannot read a run's actual assigned budget from the Registry;
it has no such field to read.

Rather than inventing a fake per-run budget value, this module exposes
the Section 9.4 **M-size default** (2 hours) as a fleet-wide reference
budget applied uniformly to every card's current-stage elapsed time, and
labels it in the UI/API as a default reference, not a per-run figure --
"elapsed vs. the Sec 9.4 M default" rather than "elapsed vs. this run's
budget." A real implementation should either (a) add a story-size /
per-stage-budget field to F2's Registry schema (a shared-contract change
per CLAUDE.md ground rule 4 -- flagged here, not made unilaterally by
this deliverable), or (b) source it from wherever story sizing is
decided (Section 4.3) via a separate lookup this dashboard would consume
read-only, same as it does the Registry.
"""

from __future__ import annotations

# Section 9.4 defaults, wall-clock, by story size. XL is intentionally
# absent -- Section 9.4: "not run directly ... a decomposition signal."
DEFAULT_BUDGET_MINUTES_BY_SIZE: dict[str, int] = {
    "S": 30,
    "M": 120,
    "L": 360,
}

# Applied uniformly to every card, since the Registry has no per-run size
# field to select S/M/L from (see module docstring).
FLEET_WIDE_REFERENCE_SIZE = "M"
REFERENCE_BUDGET_SECONDS = DEFAULT_BUDGET_MINUTES_BY_SIZE[FLEET_WIDE_REFERENCE_SIZE] * 60


def budget_fraction(elapsed_seconds: float) -> float:
    """`elapsed_seconds` as a fraction of the fleet-wide reference budget.

    Not clamped to 1.0 -- callers may want to distinguish "at 100%" from
    "over budget" (>1.0), matching Sec 9.4's 80%-checkpoint / 100%-hard-
    stop framing.
    """
    if REFERENCE_BUDGET_SECONDS <= 0:
        return 0.0
    return elapsed_seconds / REFERENCE_BUDGET_SECONDS
