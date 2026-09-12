"""End-to-end wiring: GatesService composes identity resolution, real
Jira-backed approver resolution, real CODEOWNERS evaluation,
self-approval enforcement, and escalation into the single entry point a
real caller (in place of calling D2's `Orchestrator.approve_plan`/
`approve_change_review` directly) would use."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gates.approver import ApproverIdentity
from gates.audit import AuditLog
from gates.codeowners import CodeownersResolver
from gates.escalation import EscalationTracker
from gates.gate_service import GatesService
from gates.identity import AuthenticatedIdentity, MockIdentityResolver

from ._helpers import seed_issue

FRIDAY = datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)


def _service(jira_client, codeowners_repo):
    dev_one = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")
    dev_two = AuthenticatedIdentity(subject_id="acc-dev-2", display_name="Dev Two", handles=frozenset({"@billing-team"}))
    backup = AuthenticatedIdentity(subject_id="acc-backup", display_name="On-call Lead")
    identity_resolver = MockIdentityResolver.from_identities(dev_one, dev_two, backup)

    codeowners_resolver = CodeownersResolver(repo_root_resolver=lambda repo: codeowners_repo)
    audit_log = AuditLog()
    escalation = EscalationTracker(audit_log, clock=lambda: FRIDAY)

    service = GatesService(
        jira_client=jira_client,
        identity_resolver=identity_resolver,
        codeowners_resolver=codeowners_resolver,
        audit_log=audit_log,
        escalation_tracker=escalation,
        backup_approvers={"acme/app": ApproverIdentity(account_id="acc-backup", display_name="On-call Lead", source="backup")},
    )
    return service


def test_plan_approval_gate_rejects_the_triggering_identity_then_clears_for_someone_else(jira_client, jira_mock, codeowners_repo):
    _, store = jira_mock
    seed_issue(store, key="PROJ-10", assignee={"accountId": "acc-dev-1", "displayName": "Dev One"})
    service = _service(jira_client, codeowners_repo)

    trigger = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")
    handle = service.open_gate(
        gate_id="gate-plan-1", tenant_id="tenant-acme", run_id="run-1", issue_key="PROJ-10", repo="acme/app",
        gate_kind="plan_approval", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )
    assert handle.default_approver.account_id == "acc-dev-1"

    # The assignee is both the trigger AND the resolved default
    # approver -- an entirely ordinary case -- and it must still fail.
    rejected = service.decide("gate-plan-1", credential="acc-dev-1", now=FRIDAY)
    assert rejected.cleared is False

    rejected_events = [e for e in service.audit_log.all() if e.kind == "gate_rejected"]
    assert len(rejected_events) == 1


def test_change_review_gate_requires_codeowners_reviewer_not_just_the_default_approver(jira_client, jira_mock, codeowners_repo):
    _, store = jira_mock
    seed_issue(store, key="PROJ-11", assignee={"accountId": "acc-dev-2", "displayName": "Dev Two"})
    service = _service(jira_client, codeowners_repo)

    trigger = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")
    service.open_gate(
        gate_id="gate-review-1", tenant_id="tenant-acme", run_id="run-2", issue_key="PROJ-11", repo="acme/app",
        gate_kind="change_review", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )

    # dev-two is the default approver and holds the @billing-team
    # CODEOWNERS handle for billing/service.py -> clears.
    clearance = service.decide(
        "gate-review-1", credential="acc-dev-2", touched_files=["billing/service.py"], now=FRIDAY,
    )
    assert clearance.cleared is True


def test_change_review_gate_does_not_clear_when_approver_lacks_codeowners_handle(jira_client, jira_mock, codeowners_repo):
    _, store = jira_mock
    seed_issue(store, key="PROJ-12", assignee={"accountId": "acc-dev-2", "displayName": "Dev Two"})
    service = _service(jira_client, codeowners_repo)

    trigger = AuthenticatedIdentity(subject_id="acc-dev-1", display_name="Dev One")
    service.open_gate(
        gate_id="gate-review-2", tenant_id="tenant-acme", run_id="run-3", issue_key="PROJ-12", repo="acme/app",
        gate_kind="change_review", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )

    # dev-two is the default approver but does NOT hold @reporting-team.
    clearance = service.decide(
        "gate-review-2", credential="acc-dev-2", touched_files=["reporting/report.py"], now=FRIDAY,
    )
    assert clearance.cleared is False


def test_escalation_switches_the_authorized_actor_to_the_backup_approver(jira_client, jira_mock, codeowners_repo):
    _, store = jira_mock
    seed_issue(store, key="PROJ-13", assignee={"accountId": "acc-dev-1", "displayName": "Dev One"})
    service = _service(jira_client, codeowners_repo)

    trigger = AuthenticatedIdentity(subject_id="acc-some-bot", display_name="Automation")
    service.open_gate(
        gate_id="gate-plan-2", tenant_id="tenant-acme", run_id="run-4", issue_key="PROJ-13", repo="acme/app",
        gate_kind="plan_approval", triggered_by=trigger, sla_business_days=2, opened_at=FRIDAY,
    )

    # Before the SLA boundary, the default approver (dev-1) is still
    # the authority and can clear it.
    unescalated_authority = service.escalation.current_authority("gate-plan-2", default_approver_id="acc-dev-1")
    assert unescalated_authority == "acc-dev-1"

    service.check_escalations(FRIDAY + timedelta(days=10))
    escalated_authority = service.escalation.current_authority("gate-plan-2", default_approver_id="acc-dev-1")
    assert escalated_authority == "acc-backup"

    clearance = service.decide("gate-plan-2", credential="acc-backup", now=FRIDAY + timedelta(days=10))
    assert clearance.cleared is True
    assert clearance.approver_id == "acc-backup"
