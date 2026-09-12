"""Self-approval disallowed by construction (master spec Sec. 12.1,
17.2) -- the marquee acceptance criteria for this deliverable:

  - "The identity that triggered a run cannot approve its own
    plan-approval gate, verified with a test case."
  - "A change-review gate without a CODEOWNERS-satisfying reviewer does
    not clear, even if the default approver acts."
"""
from __future__ import annotations

import pytest

from gates.approver import ApproverIdentity
from gates.codeowners import OwnershipRequirement
from gates.identity import AuthenticatedIdentity
from gates.self_approval import SelfApprovalError, evaluate_gate_clearance, require_cleared

TRIGGER = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")
SAME_PERSON_AS_TRIGGER = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")
OTHER_DEV = AuthenticatedIdentity(subject_id="acc-dev-2", display_name="Dev Two", handles=frozenset({"@dev-two"}))

APPROVER_IS_TRIGGER = ApproverIdentity(account_id="acc-dev-1", display_name="Dev One", source="assignee")
APPROVER_IS_OTHER_DEV = ApproverIdentity(account_id="acc-dev-2", display_name="Dev Two", source="assignee")


def test_trigger_identity_cannot_approve_its_own_plan_approval_gate():
    """AC1: even though the resolved default approver (Jira Assignee)
    IS the identity that triggered the run -- an entirely ordinary
    real-world case -- the gate must not clear when that same identity
    is the one acting on it."""
    clearance = evaluate_gate_clearance(
        gate_kind="plan_approval",
        run_id="run-1",
        triggered_by=TRIGGER,
        actor=SAME_PERSON_AS_TRIGGER,
        approver=APPROVER_IS_TRIGGER,
    )
    assert clearance.cleared is False
    assert any("self-approval is disallowed by construction" in r for r in clearance.reasons)

    with pytest.raises(SelfApprovalError):
        require_cleared(clearance)


def test_a_different_actor_who_is_the_resolved_approver_can_clear_plan_approval():
    """Positive control: the same gate clears once a genuinely
    different identity -- who is also the resolved approver -- acts."""
    clearance = evaluate_gate_clearance(
        gate_kind="plan_approval",
        run_id="run-1",
        triggered_by=TRIGGER,
        actor=OTHER_DEV,
        approver=APPROVER_IS_OTHER_DEV,
    )
    assert clearance.cleared is True
    assert clearance.reasons == ()
    require_cleared(clearance)  # does not raise


def test_actor_who_is_not_the_resolved_approver_cannot_clear_even_if_not_the_trigger():
    """Being a different person than the trigger is necessary but not
    sufficient -- the actor must also actually be the gate's resolved
    authority (default approver, or backup after escalation)."""
    random_third_party = AuthenticatedIdentity(subject_id="acc-random", display_name="Random")
    clearance = evaluate_gate_clearance(
        gate_kind="plan_approval",
        run_id="run-1",
        triggered_by=TRIGGER,
        actor=random_third_party,
        approver=APPROVER_IS_OTHER_DEV,
    )
    assert clearance.cleared is False
    assert any("not the gate's resolved authority" in r for r in clearance.reasons)


def test_change_review_gate_without_codeowners_reviewer_does_not_clear_even_for_default_approver():
    """AC2: the default approver acting, alone, is not enough for a
    change-review gate -- a CODEOWNERS-satisfying reviewer is also
    required, checked independently of who the acting approver is."""
    requirement = OwnershipRequirement(
        repo="acme/app", owners_by_file={"billing/service.py": frozenset({"@billing-team"})}
    )
    clearance = evaluate_gate_clearance(
        gate_kind="change_review",
        run_id="run-2",
        triggered_by=TRIGGER,
        actor=OTHER_DEV,  # a genuine, non-triggering, correctly-resolved approver ...
        approver=APPROVER_IS_OTHER_DEV,
        ownership_requirement=requirement,  # ... but does not hold the "@billing-team" handle
    )
    assert clearance.cleared is False
    assert any("does not satisfy CODEOWNERS" in r for r in clearance.reasons)


def test_change_review_gate_omitting_the_ownership_requirement_fails_closed():
    clearance = evaluate_gate_clearance(
        gate_kind="change_review",
        run_id="run-2",
        triggered_by=TRIGGER,
        actor=OTHER_DEV,
        approver=APPROVER_IS_OTHER_DEV,
        ownership_requirement=None,
    )
    assert clearance.cleared is False
    assert any("requires a CODEOWNERS-satisfying reviewer" in r for r in clearance.reasons)


def test_change_review_gate_clears_when_codeowners_satisfied_and_actor_is_not_the_trigger():
    requirement = OwnershipRequirement(
        repo="acme/app", owners_by_file={"billing/service.py": frozenset({"@dev-two"})}
    )
    clearance = evaluate_gate_clearance(
        gate_kind="change_review",
        run_id="run-2",
        triggered_by=TRIGGER,
        actor=OTHER_DEV,
        approver=APPROVER_IS_OTHER_DEV,
        ownership_requirement=requirement,
    )
    assert clearance.cleared is True


def test_change_review_still_blocks_self_approval_even_with_codeowners_satisfied():
    """The self-approval rule and the CODEOWNERS rule are independent:
    satisfying one never substitutes for the other."""
    requirement = OwnershipRequirement(
        repo="acme/app", owners_by_file={"billing/service.py": frozenset({"@dev-one"})}
    )
    trigger_with_handle = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One", handles=frozenset({"@dev-one"}))
    clearance = evaluate_gate_clearance(
        gate_kind="change_review",
        run_id="run-2",
        triggered_by=TRIGGER,
        actor=trigger_with_handle,  # same subject_id as TRIGGER -- self-approval
        approver=APPROVER_IS_TRIGGER,
        ownership_requirement=requirement,
    )
    assert clearance.cleared is False
    assert any("self-approval" in r for r in clearance.reasons)
