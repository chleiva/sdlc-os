"""Dashboard column <-> Run Registry stage mapping (master spec Sec.
16.5, quoted verbatim in this deliverable's brief, wave1-D8-fleet-
dashboard.md).

This is the ONE mapping table the brief requires be implemented exactly;
it is expressed here as plain data so `cards.py` and the tests share a
single definition rather than each hand-rolling the stage lists.

    Dashboard column | Pipeline stage(s)
    -----------------|--------------------------------------------
    Analysis         | intake, research
    Design           | plan_authoring, plan_approval_gate
    Development      | implementation
    Testing          | verification
    PR Review        | change_review_gate
    Commit           | packaging, retrospective

`completed` and `retrospective`-cleared runs are terminal (Section
14.14's terminal pair is `completed`/`abandoned`); a Run in a terminal
stage is not shown on the board at all -- "Commit ... clears the board
once the retrospective is recorded" (brief, Commit column notes). This
module treats `completed` as "cleared" and `abandoned` as "cleared" the
same way: the board is a view of *in-flight* runs, not a full history
(the Attempt/stage history is still queryable through the Registry
Service for anyone who wants it -- this dashboard just doesn't render it
as a card).
"""

from __future__ import annotations

from run_registry import stages

ANALYSIS = "Analysis"
DESIGN = "Design"
DEVELOPMENT = "Development"
TESTING = "Testing"
PR_REVIEW = "PR Review"
COMMIT = "Commit"

COLUMNS: tuple[str, ...] = (ANALYSIS, DESIGN, DEVELOPMENT, TESTING, PR_REVIEW, COMMIT)

# Stage -> column. Exhaustive over every non-terminal stage in stages.py;
# a KeyError here for any real Registry stage would mean this mapping has
# drifted from F2's fixed vocabulary, which is exactly the kind of silent
# divergence CLAUDE.md's "one rule" warns about -- see
# `test_column_mapping.py::test_mapping_is_exhaustive_over_registry_stages`.
_STAGE_TO_COLUMN: dict[str, str] = {
    stages.INTAKE: ANALYSIS,
    stages.RESEARCH: ANALYSIS,
    stages.PLAN_AUTHORING: DESIGN,
    stages.PLAN_APPROVAL_GATE: DESIGN,
    stages.IMPLEMENTATION: DEVELOPMENT,
    stages.VERIFICATION: TESTING,
    stages.CHANGE_REVIEW_GATE: PR_REVIEW,
    stages.PACKAGING: COMMIT,
    stages.RETROSPECTIVE: COMMIT,
}

# Terminal stages are never mapped to a column -- they clear the board.
TERMINAL_STAGES = frozenset(stages.TERMINAL_STAGES)


def column_for_stage(stage: str) -> str | None:
    """The board column for `stage`, or None if `stage` is terminal (not
    shown on the board) or not a recognized Registry stage at all (should
    never happen against a real Registry, but we fail closed to "no
    column" rather than crash on an unrecognized value).
    """
    if stage in TERMINAL_STAGES:
        return None
    return _STAGE_TO_COLUMN.get(stage)


def is_shown_on_board(stage: str) -> bool:
    return column_for_stage(stage) is not None
