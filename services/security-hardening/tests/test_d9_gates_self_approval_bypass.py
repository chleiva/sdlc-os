"""D10 adversarial pass on D9 (gates/governance) self-approval.

`services/gates/` landed mid-way through this D10 pass (it did not
exist when this deliverable's brief was written, which explicitly says
to skip-and-note if that's the case; it is no longer the case, so this
replaces the skip note with the real adversarial test the brief asks
for). D9's own `test_self_approval.py` already thoroughly unit-tests
`evaluate_gate_clearance` in isolation; this file does NOT re-run that
-- it drives the REAL `GatesService` end to end (real Jira mock via
D4's `JiraClient`, real `EscalationTracker`, real `CodeownersResolver`)
and specifically tries to CONSTRUCT a bypass, per the brief's
instruction, rather than only re-confirming the happy/unhappy paths D9
already covers:

  1. The ordinary case D9 covers (trigger == default approver) is
     re-proven end to end through `GatesService.decide`, not just the
     lower-level `evaluate_gate_clearance` function, as a baseline.
  2. A misconfigured backup approver that happens to collide with the
     triggering identity (a realistic operational mistake) must still
     be rejected after escalation -- rule 1 is unconditional regardless
     of who the resolved authority is.
  3. Once a gate has cleared (removed from `GatesService._open_gates`),
     attempting to `decide()` it again must fail closed (`GateNotFoundError`),
     never silently "re-approve."
  4. A STRUCTURAL FINDING, documented and proven, NOT patched: the only
     "single choke point" self-approval is enforced at is
     `GatesService.decide()` (which calls `evaluate_gate_clearance`).
     `GatesService.escalation` is a public attribute (relied upon by
     D9's own `test_gate_service_integration.py::
     test_escalation_switches_the_authorized_actor_to_the_backup_approver`,
     which reads `service.escalation.current_authority(...)` directly),
     and `EscalationTracker.mark_actioned(gate_id)` performs NO actor/
     clearance check at all -- any code in the same process holding a
     `GatesService` reference can call
     `gates_service.escalation.mark_actioned(gate_id)` directly and mark
     a gate "actioned" in the audit/escalation bookkeeping without ANY
     approval ever having been evaluated, completely bypassing
     self-approval, CODEOWNERS, and identity resolution.

     This is NOT patched here because: (a) there is no network/MCP
     boundary anywhere in `services/gates/` yet (confirmed: no
     `mcp_server.py`/`http_app.py` exists in that package) -- exactly
     the same in-process-only threat model as the already-documented,
     unpatched `orchestrator.sandbox`/`orchestrator.worktree` Hook-chain
     bypasses this pass found and deliberately left alone; (b) making
     `mark_actioned` itself require a `GateClearance` would cross
     `escalation.py`'s own intentionally decoupled "SLA bookkeeping
     only" module boundary (its own docstring: "this module owns the
     durable bookkeeping ... plus the actual escalation decision" --
     explicitly NOT the self-approval decision) and would break its own
     already-passing standalone unit tests
     (`test_escalation.py::test_...` calls `mark_actioned` directly with
     no clearance, by design, to test SLA bookkeeping in isolation); and
     (c) renaming `self.escalation` to a private attribute would break
     `test_gate_service_integration.py`'s own existing, passing use of
     the public attribute. Flagged prominently in the final report for a
     human to resolve -- e.g. by adding an actual network/MCP boundary
     for `GatesService` later that exposes ONLY `decide()`/`open_gate()`/
     `check_escalations()`, never the raw `escalation` attribute.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SERVICES_ROOT = Path(__file__).resolve().parent.parent.parent
ISSUE_TRACKER_ROOT = SERVICES_ROOT / "issue-tracker"
if str(ISSUE_TRACKER_ROOT) not in sys.path:
    sys.path.insert(0, str(ISSUE_TRACKER_ROOT))

from mocks.jira_mock_server import MockIssue, start_mock_server as start_jira_mock  # noqa: E402
from issue_tracker.config import TenantJiraConfig  # noqa: E402
from issue_tracker.jira_client import JiraClient  # noqa: E402

from gates.approver import ApproverIdentity
from gates.audit import AuditLog
from gates.escalation import EscalationTracker
from gates.gate_service import GateNotFoundError, GatesService
from gates.identity import AuthenticatedIdentity, MockIdentityResolver
from gates.self_approval import evaluate_gate_clearance

FRIDAY = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def jira_mock():
    server, store = start_jira_mock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}", store
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.fixture
def jira_client(jira_mock):
    base_url, _store = jira_mock
    config = TenantJiraConfig(
        tenant_id="tenant-acme", base_url=base_url, auth_mode="oauth_bearer",
        oauth_bearer_token="test-token", project_key="PROJ",
    )
    return JiraClient(config=config)


def _seed(store, key, assignee_account_id):
    store.seed(
        MockIssue(
            key=key, project_key="PROJ", issue_type="Story", summary="s",
            description={"type": "doc", "version": 1, "content": []},
            fields_extra={"assignee": {"accountId": assignee_account_id, "displayName": "x"}},
        )
    )


def _service(jira_client, *, backup_approvers=None):
    trigger_id = "acc-dev-1"
    identities = [
        AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One"),
        AuthenticatedIdentity(subject_id="acc-dev-2", display_name="Dev Two"),
        AuthenticatedIdentity(subject_id="acc-backup", display_name="Backup"),
    ]
    identity_resolver = MockIdentityResolver.from_identities(*identities)
    audit_log = AuditLog()
    escalation = EscalationTracker(audit_log, clock=lambda: FRIDAY)
    service = GatesService(
        jira_client=jira_client,
        identity_resolver=identity_resolver,
        audit_log=audit_log,
        escalation_tracker=escalation,
        backup_approvers=backup_approvers or {"acme/app": ApproverIdentity(account_id="acc-backup", display_name="Backup", source="backup")},
    )
    return service


# ---------------------------------------------------------------------
# 1. Baseline end-to-end proof through the real GatesService choke point.
# ---------------------------------------------------------------------
def test_end_to_end_self_approval_is_rejected_through_gates_service(jira_client, jira_mock):
    _, store = jira_mock
    _seed(store, "PROJ-1", assignee_account_id="acc-dev-1")
    service = _service(jira_client)
    trigger = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")

    service.open_gate(
        gate_id="g1", tenant_id="t1", run_id="r1", issue_key="PROJ-1", repo="acme/app",
        gate_kind="plan_approval", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )
    clearance = service.decide("g1", credential="acc-dev-1", now=FRIDAY)
    assert clearance.cleared is False


# ---------------------------------------------------------------------
# 2. Real adversarial attempt: a misconfigured backup approver that
#    happens to be the SAME identity as the run's trigger. Escalation
#    reassigns authority to this backup; self-approval must still be
#    caught, because rule 1 is unconditional regardless of who the
#    resolved authority currently is.
# ---------------------------------------------------------------------
def test_backup_approver_colliding_with_trigger_identity_is_still_rejected_after_escalation(jira_client, jira_mock):
    _, store = jira_mock
    _seed(store, "PROJ-2", assignee_account_id="acc-dev-2")  # someone else is the nominal assignee
    misconfigured_backup = {"acme/app": ApproverIdentity(account_id="acc-dev-1", display_name="Dev One", source="backup")}
    service = _service(jira_client, backup_approvers=misconfigured_backup)

    trigger = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")  # SAME as the misconfigured backup
    service.open_gate(
        gate_id="g2", tenant_id="t1", run_id="r2", issue_key="PROJ-2", repo="acme/app",
        gate_kind="plan_approval", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )
    # SLA breach -> authority moves to the (misconfigured, trigger-
    # colliding) backup approver.
    service.check_escalations(FRIDAY + timedelta(days=10))

    clearance = service.decide("g2", credential="acc-dev-1", now=FRIDAY + timedelta(days=10))
    assert clearance.cleared is False, "self-approval must be caught even via a misconfigured, trigger-colliding backup approver"
    assert any("self-approval" in r for r in clearance.reasons)


# ---------------------------------------------------------------------
# 2b. REGRESSION TEST for the real self-approval bypass this pass FOUND
#     and FIXED (`gates.self_approval._normalized_identity`, wired into
#     `evaluate_gate_clearance`): `triggered_by` (captured once, at
#     open_gate time) and `actor` (independently re-resolved from a
#     credential at decide time) are two structurally different code
#     paths that, before the fix, never had to agree on casing/
#     whitespace for the SAME real human -- letting the same person
#     clear their own gate if their identity string merely differed in
#     case between the two capture points.
# ---------------------------------------------------------------------
def test_case_and_whitespace_variant_of_the_same_human_still_blocked_at_the_pure_logic_layer():
    triggered_by = AuthenticatedIdentity(subject_id="ACC-DEV-1", display_name="Dev One")  # as captured at trigger time
    actor = AuthenticatedIdentity(subject_id=" acc-dev-1", display_name="Dev One")  # same human, re-resolved differently
    approver = ApproverIdentity(account_id="acc-dev-1", display_name="Dev One", source="assignee")

    clearance = evaluate_gate_clearance(
        gate_kind="plan_approval", run_id="run-1", triggered_by=triggered_by, actor=actor, approver=approver,
    )
    assert clearance.cleared is False, "a case/whitespace-variant representation of the SAME human bypassed self-approval"
    assert any("self-approval" in r for r in clearance.reasons)


def test_case_variant_trigger_identity_still_blocked_end_to_end_through_gates_service(jira_client, jira_mock):
    _, store = jira_mock
    _seed(store, "PROJ-5", assignee_account_id="acc-dev-1")
    service = _service(jira_client)

    # The run was triggered by "Acc-Dev-1" (some upstream system's own
    # casing), but the credential the same human later authenticates
    # with resolves (via MockIdentityResolver) to the lowercase
    # "acc-dev-1" identity already registered in `_service`.
    trigger = AuthenticatedIdentity(subject_id="Acc-Dev-1", display_name="Dev One")
    service.open_gate(
        gate_id="g5", tenant_id="t1", run_id="r5", issue_key="PROJ-5", repo="acme/app",
        gate_kind="plan_approval", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )
    clearance = service.decide("g5", credential="acc-dev-1", now=FRIDAY)
    assert clearance.cleared is False


# ---------------------------------------------------------------------
# 3. Once cleared, a gate cannot be "decided" again -- no double-spend /
#    re-approval path.
# ---------------------------------------------------------------------
def test_a_cleared_gate_cannot_be_decided_again(jira_client, jira_mock):
    _, store = jira_mock
    _seed(store, "PROJ-3", assignee_account_id="acc-dev-2")
    service = _service(jira_client)
    trigger = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")
    service.open_gate(
        gate_id="g3", tenant_id="t1", run_id="r3", issue_key="PROJ-3", repo="acme/app",
        gate_kind="plan_approval", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )
    clearance = service.decide("g3", credential="acc-dev-2", now=FRIDAY)
    assert clearance.cleared is True

    with pytest.raises(GateNotFoundError):
        service.decide("g3", credential="acc-dev-1", now=FRIDAY)  # even the trigger trying again finds nothing to decide


# ---------------------------------------------------------------------
# 4. STRUCTURAL FINDING (documented, not patched -- see module
#    docstring for why): a direct call to the publicly-reachable
#    `escalation.mark_actioned` bypasses evaluate_gate_clearance
#    entirely.
# ---------------------------------------------------------------------
def test_STRUCTURAL_FINDING_gate_clearance_can_be_hand_fabricated(jira_client, jira_mock):
    """STRUCTURAL FINDING (documented, not patched): `self_approval.py`'s
    own module docstring claims `GateClearance` "can only be constructed
    by `evaluate_gate_clearance`... there is no other constructor path."
    That claim is a comment, not a language-level guarantee -- Python
    dataclasses have no private constructor, so any caller that imports
    `gates.self_approval` can build a `cleared=True` `GateClearance`
    directly and hand it to `require_cleared`, which only checks
    `.cleared` and never re-derives anything from the raw identities.

    NOT patched here: this is the same "nominal type" idiom used
    throughout this whole codebase (D1's `AuthenticatedTrigger`, D4's
    `OptedInStory`, D9's own `GateClearance`) -- none of them have a
    true private constructor in Python either, so this is a systemic,
    pre-existing property of the pattern, not a defect unique to D9 that
    a small D10 patch could close without addressing the same "claim"
    everywhere else it's made. Flagged in the final report."""
    from gates.self_approval import GateClearance, require_cleared

    forged = GateClearance(
        gate_kind="plan_approval", run_id="run-forged", actor_id="acc-dev-1",
        approver_id="acc-dev-1", cleared=True, reasons=(),
    )
    # This does NOT raise -- proving the fabrication "works" against the
    # real code exactly as documented above.
    result = require_cleared(forged)
    assert result.cleared is True


def test_STRUCTURAL_FINDING_direct_escalation_mark_actioned_bypasses_self_approval(jira_client, jira_mock):
    _, store = jira_mock
    _seed(store, "PROJ-4", assignee_account_id="acc-dev-2")
    service = _service(jira_client)
    trigger = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")
    service.open_gate(
        gate_id="g4", tenant_id="t1", run_id="r4", issue_key="PROJ-4", repo="acme/app",
        gate_kind="plan_approval", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )

    # No human ever decided anything -- the gate is still genuinely open.
    assert "g4" in service._open_gates

    # A bypass: reach through the PUBLIC `escalation` attribute directly,
    # skipping `decide()`/`evaluate_gate_clearance` entirely.
    service.escalation.mark_actioned("g4", actioned_at=FRIDAY)

    # The escalation tracker's own bookkeeping now believes this gate
    # was actioned -- with NO self-approval or CODEOWNERS check having
    # run, and no audit "gate_rejected"/clearance record produced at
    # all. This is the real, provable gap: anything downstream that
    # trusts `EscalationTracker`'s "actioned" state as a proxy for "a
    # human legitimately approved this" (rather than exclusively
    # trusting `decide()`'s returned `GateClearance.cleared`) would be
    # fooled.
    with pytest.raises(Exception):  # AlreadyActionedError -- confirms the bypass really landed
        service.escalation.mark_actioned("g4", actioned_at=FRIDAY)

    # Meanwhile `GatesService`'s OWN bookkeeping (`_open_gates`) was
    # untouched by this bypass -- only `decide()`'s cleared branch
    # removes a gate from it. This is precisely why any real caller
    # MUST treat `decide()`'s return value (or continued presence in
    # `_open_gates`) as the only ground truth, never the escalation
    # tracker's "actioned" flag in isolation.
    assert "g4" in service._open_gates, (
        "GatesService's own open-gate bookkeeping is unaffected by the bypass -- "
        "confirming decide() remains the only path that actually clears a gate"
    )
