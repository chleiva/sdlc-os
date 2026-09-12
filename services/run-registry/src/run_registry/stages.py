"""The fixed nine-stage-plus-terminal vocabulary and its legal transitions.

Master spec Section 5 defines nine stages every story moves through;
Section 14.14 fixes the Run Registry's stage vocabulary to those nine
stages plus one terminal pair (Completed, Abandoned), and requires the
Registry Service to validate every transition against this vocabulary
before writing it -- an illegal transition is rejected, never silently
accepted.

ASSUMPTION FLAGGED FOR HUMAN REVIEW: Section 5 and Section 14.14 name the
nine stages and state two transitions explicitly --
(1) a Verification (stage 6) failure "loops back to implementation"
    (stage 5), and
(2) the Plan-approval gate (stage 4) can approve (-> Implementation),
    request changes, or reject.
Neither section gives an exhaustive transition table, so the following
choices are this implementation's own reasonable interpretation, not
verbatim spec text -- a human should confirm them against product intent
before this becomes load-bearing for other deliverables:

  * plan_approval_gate "request changes" -> plan_authoring. Section 9.2
    is the "re-plan after request changes" scenario that Section 14.14
    names as one of the three triggers for a new Attempt, which is why
    this specific loop-back (unlike the change-review one below) also
    starts a new Attempt with reason "re-plan" -- modeled in
    `service.py`, not here.
  * plan_approval_gate "reject" -> abandoned.
  * change_review_gate "request changes" -> implementation. Section 5
    Stage 7 describes this as reviewing "a diff" against the plan, so
    the loop-back target is the stage that produces the diff
    (implementation), not plan_authoring. Section 14.14 does not name
    this as one of its three explicit new-Attempt triggers, so this
    implementation treats it as a same-Attempt stage regression rather
    than the start of a new Attempt -- also worth confirming.
  * Any non-terminal stage -> abandoned. The spec discusses abandonment
    only implicitly (a stuck retry budget exhausted, an explicit
    cancellation) without naming which stages it can occur from; this
    implementation allows it from anywhere non-terminal, since gating
    that further isn't specified anywhere.
  * completed and abandoned are terminal: no legal outgoing transition.

None of this affects the *vocabulary* (which is exact per spec) or the
Attempt/version/tenant-scoping mechanics -- only the specific edges of the
transition graph.
"""

from __future__ import annotations

# Order matches Section 5's numbering exactly.
INTAKE = "intake"
RESEARCH = "research"
PLAN_AUTHORING = "plan_authoring"
PLAN_APPROVAL_GATE = "plan_approval_gate"
IMPLEMENTATION = "implementation"
VERIFICATION = "verification"
CHANGE_REVIEW_GATE = "change_review_gate"
PACKAGING = "packaging"
RETROSPECTIVE = "retrospective"

# Terminal pair, Section 14.14.
COMPLETED = "completed"
ABANDONED = "abandoned"

STAGES: tuple[str, ...] = (
    INTAKE,
    RESEARCH,
    PLAN_AUTHORING,
    PLAN_APPROVAL_GATE,
    IMPLEMENTATION,
    VERIFICATION,
    CHANGE_REVIEW_GATE,
    PACKAGING,
    RETROSPECTIVE,
)

TERMINAL_STAGES: tuple[str, ...] = (COMPLETED, ABANDONED)

ALL_STAGES: tuple[str, ...] = STAGES + TERMINAL_STAGES

NON_TERMINAL_STAGES: tuple[str, ...] = STAGES

# The legal-next-step vocabulary. Keys are the current stage; values are
# the set of stages a transition may legally move to from there. See the
# module docstring for which edges are spec-explicit versus this
# implementation's interpretation.
_LEGAL_TRANSITIONS: dict[str, frozenset[str]] = {
    INTAKE: frozenset({RESEARCH, ABANDONED}),
    RESEARCH: frozenset({PLAN_AUTHORING, ABANDONED}),
    PLAN_AUTHORING: frozenset({PLAN_APPROVAL_GATE, ABANDONED}),
    PLAN_APPROVAL_GATE: frozenset({IMPLEMENTATION, PLAN_AUTHORING, ABANDONED}),
    IMPLEMENTATION: frozenset({VERIFICATION, ABANDONED}),
    VERIFICATION: frozenset({CHANGE_REVIEW_GATE, IMPLEMENTATION, ABANDONED}),
    CHANGE_REVIEW_GATE: frozenset({PACKAGING, IMPLEMENTATION, ABANDONED}),
    PACKAGING: frozenset({RETROSPECTIVE, ABANDONED}),
    RETROSPECTIVE: frozenset({COMPLETED, ABANDONED}),
    COMPLETED: frozenset(),
    ABANDONED: frozenset(),
}

# Restart triggers that Section 14.14 explicitly names as starting a new
# Attempt (as opposed to a same-Attempt stage regression). Used by
# service.py to decide whether a stage transition alone is sufficient or
# whether a new Attempt must be appended first.
NEW_ATTEMPT_TRIGGERING_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        (PLAN_APPROVAL_GATE, PLAN_AUTHORING),  # re-plan, Section 9.2
    }
)


def is_valid_stage(stage: str) -> bool:
    return stage in ALL_STAGES


def is_legal_transition(current_stage: str, next_stage: str) -> bool:
    """True iff `next_stage` is a legal next step from `current_stage`.

    Unknown stages are never legal (fixed vocabulary only, Section 14.12:
    "never a free-text status string").
    """
    if not is_valid_stage(current_stage) or not is_valid_stage(next_stage):
        return False
    return next_stage in _LEGAL_TRANSITIONS.get(current_stage, frozenset())
