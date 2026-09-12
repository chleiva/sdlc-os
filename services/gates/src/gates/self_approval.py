"""Self-approval, disallowed by construction (master spec Sec. 12.1,
17.2): "the identity that triggered the run is never, by itself,
sufficient to satisfy a gate it triggered" and "the System cannot
approve its own gates under any autonomy level."

ASSUMPTION FLAGGED FOR HUMAN REVIEW: `ApproverIdentity.account_id`
(Jira's identity namespace) and `AuthenticatedIdentity.subject_id`
(OIDC's identity namespace, Sec. 17.3) are compared directly below as
if they were the same identifier space. A real deployment needs an
explicit Jira-accountId <-> OIDC-subject mapping (the same human has two
different ids in the two systems); this deliverable has no live IdP or
live Jira org to derive that mapping from, so it is left as a seam a
human/Wave-3-integration must fill in -- every test in this deliverable
constructs its `AuthenticatedIdentity.subject_id` values to already
equal the corresponding mock Jira `accountId`, which is honest about
what is proven here (the clearance *logic*) versus what still needs
real-world identity federation.

Same discipline as D1's `tenant_resolution.ResolvedTrigger` and D4's
`gating.evaluate`/`OptedInStory`: a nominal type (`GateClearance`) that
can only be constructed by `evaluate_gate_clearance` below -- there is
no other constructor path in this module or exported from it, so no
caller can fabricate "this gate cleared" and skip the self-approval (or
CODEOWNERS) check. `evaluate_gate_clearance` re-derives the decision
from the raw identities every time, rather than trusting a
caller-supplied boolean.
"""

from __future__ import annotations

from dataclasses import dataclass

from gates.approver import ApproverIdentity
from gates.codeowners import OwnershipRequirement
from gates.identity import AuthenticatedIdentity


@dataclass(frozen=True)
class GateClearance:
    """Evidence that a specific actor's decision on a specific gate was
    structurally permitted to clear it. The only way to obtain one is
    `evaluate_gate_clearance` returning it (see module docstring);
    dataclass equality/repr make a clearance's `cleared`/`reasons`
    fields independently inspectable in tests rather than an opaque
    bool.
    """

    gate_kind: str  # "plan_approval" | "change_review"
    run_id: str
    actor_id: str
    approver_id: str
    cleared: bool
    reasons: tuple[str, ...]


class SelfApprovalError(Exception):
    """Raised by `require_cleared` (never by `evaluate_gate_clearance`,
    which always returns a `GateClearance` -- clearing or not -- so a
    caller can inspect *why* before deciding what to do)."""


def evaluate_gate_clearance(
    *,
    gate_kind: str,
    run_id: str,
    triggered_by: AuthenticatedIdentity,
    actor: AuthenticatedIdentity,
    approver: ApproverIdentity,
    ownership_requirement: OwnershipRequirement | None = None,
) -> GateClearance:
    """The single source of truth for "may `actor`'s decision clear
    this gate". Three independent rules, all of which must hold:

    1. Sec. 12.1/17.2: `actor` is never the run's own trigger identity,
       full stop -- this is checked first and unconditionally, for
       every gate kind, regardless of who the resolved approver is.
    2. `actor` must be the gate's resolved authority: the default
       approver (Assignee, else Reporter) or -- once escalation.py has
       fired -- the configured backup approver. Escalation is handled
       by the caller re-resolving `approver` to the backup identity
       before calling this function again; this function itself never
       distinguishes "was this an escalation" -- it only ever compares
       against whichever `approver` it was given.
    3. Change-review gates additionally require a CODEOWNERS-satisfying
       reviewer for every touched file (Sec. 12.1) -- checked against
       `ownership_requirement.is_satisfied_by(actor.handles)` when an
       `ownership_requirement` is supplied. A change-review call made
       without one is treated as *not* satisfying rule 3 (fail-closed:
       omitting the requirement can never accidentally clear a
       change-review gate).
    """
    reasons: list[str] = []

    if actor.subject_id == triggered_by.subject_id:
        reasons.append(
            "actor is the run's own trigger identity -- self-approval is disallowed by construction "
            "(Sec. 12.1, 17.2), regardless of any other authorization the actor may hold"
        )

    if actor.subject_id != approver.account_id:
        reasons.append(
            f"actor {actor.subject_id!r} is not the gate's resolved authority "
            f"{approver.account_id!r} (source={approver.source!r})"
        )

    if gate_kind == "change_review":
        if ownership_requirement is None:
            reasons.append("change-review gate requires a CODEOWNERS-satisfying reviewer, but none was checked")
        else:
            unsatisfied = ownership_requirement.unsatisfied_files(actor.handles)
            if unsatisfied:
                reasons.append(
                    f"actor does not satisfy CODEOWNERS for file(s): {sorted(unsatisfied)}"
                )

    return GateClearance(
        gate_kind=gate_kind,
        run_id=run_id,
        actor_id=actor.subject_id,
        approver_id=approver.account_id,
        cleared=not reasons,
        reasons=tuple(reasons),
    )


def require_cleared(clearance: GateClearance) -> GateClearance:
    """Raises if `clearance` did not clear; returns it unchanged
    otherwise, so a caller can chain `require_cleared(evaluate_gate_
    clearance(...))` when it wants a hard failure rather than a
    boolean to branch on."""
    if not clearance.cleared:
        raise SelfApprovalError(
            f"gate {clearance.gate_kind!r} for run {clearance.run_id!r} did not clear for actor "
            f"{clearance.actor_id!r}: {'; '.join(clearance.reasons)}"
        )
    return clearance
