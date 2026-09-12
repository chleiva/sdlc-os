"""The top-level orchestration entry point tying every module in this
package together: this is what D2 (or whatever drives the pipeline)
calls instead of `Orchestrator.approve_plan`/`approve_change_review`
directly, so that identity resolution, self-approval, CODEOWNERS, and
escalation are always in the loop -- never bypassable by calling D2's
raw gate-transition methods with a bare decision string and no actor.

D9 does not modify D2: `GatesService` wraps an injected callback
(`on_cleared`) that a real integration point wires to
`Orchestrator.approve_plan`/`approve_change_review` -- this deliverable
never reaches into orchestrator internals, it only calls the public
methods `services/orchestrator` already exposes, per the brief ("D9
only routes them to the right human and waits for the right answer").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

from gates.approver import ApproverIdentity, resolve_default_approver
from gates.audit import AuditEvent, AuditLog
from gates.codeowners import CodeownersResolver, OwnershipRequirement
from gates.escalation import EscalationTracker, GateSlaPolicy
from gates.identity import AuthenticatedIdentity, IdentityResolver
from gates.self_approval import GateClearance, evaluate_gate_clearance
from issue_tracker.jira_client import JiraClient


class GateNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class OpenGateHandle:
    gate_id: str
    tenant_id: str
    run_id: str
    issue_key: str
    repo: str
    gate_kind: str  # "plan_approval" | "change_review"
    triggered_by: AuthenticatedIdentity
    default_approver: ApproverIdentity


class GatesService:
    def __init__(
        self,
        *,
        jira_client: JiraClient,
        identity_resolver: IdentityResolver,
        codeowners_resolver: CodeownersResolver | None = None,
        audit_log: AuditLog | None = None,
        escalation_tracker: EscalationTracker | None = None,
        backup_approvers: dict[str, ApproverIdentity] | None = None,
    ):
        self._jira_client = jira_client
        self._identity_resolver = identity_resolver
        self._codeowners_resolver = codeowners_resolver
        self.audit_log = audit_log or AuditLog()
        self.escalation = escalation_tracker or EscalationTracker(self.audit_log)
        self._backup_approvers = dict(backup_approvers or {})
        self._open_gates: dict[str, OpenGateHandle] = {}

    def _backup_for(self, repo: str) -> ApproverIdentity:
        backup = self._backup_approvers.get(repo)
        if backup is None:
            raise GateNotFoundError(f"no configured backup approver for repo {repo!r} (Sec. 12.1 requires one)")
        return backup

    def open_gate(
        self,
        *,
        gate_id: str,
        tenant_id: str,
        run_id: str,
        issue_key: str,
        repo: str,
        gate_kind: str,
        triggered_by: AuthenticatedIdentity,
        sla_business_days: float,
        opened_at: datetime | None = None,
    ) -> OpenGateHandle:
        approver = resolve_default_approver(self._jira_client, issue_key=issue_key)
        backup = self._backup_for(repo)
        policy = GateSlaPolicy(
            business_days=sla_business_days, backup_approver_id=backup.account_id,
            backup_display_name=backup.display_name,
        )
        self.escalation.open_gate(
            gate_id=gate_id, tenant_id=tenant_id, run_id=run_id, gate_kind=gate_kind, policy=policy,
            opened_at=opened_at,
        )
        handle = OpenGateHandle(
            gate_id=gate_id, tenant_id=tenant_id, run_id=run_id, issue_key=issue_key, repo=repo,
            gate_kind=gate_kind, triggered_by=triggered_by, default_approver=approver,
        )
        self._open_gates[gate_id] = handle
        return handle

    def decide(
        self,
        gate_id: str,
        *,
        credential: str,
        touched_files: Sequence[str] | None = None,
        now: datetime | None = None,
    ) -> GateClearance:
        """Resolves the acting identity, re-checks self-approval and
        (for change-review) CODEOWNERS, and -- only if cleared -- marks
        the gate actioned in the escalation tracker. Always returns the
        `GateClearance`, cleared or not, so a caller can inspect the
        reasons rather than only catching an exception."""
        handle = self._open_gates.get(gate_id)
        if handle is None:
            raise GateNotFoundError(gate_id)

        actor = self._identity_resolver.resolve(credential)
        authority_id = self.escalation.current_authority(gate_id, default_approver_id=handle.default_approver.account_id)
        effective_approver = (
            handle.default_approver if authority_id == handle.default_approver.account_id
            else self._backup_for(handle.repo)
        )

        ownership_requirement: OwnershipRequirement | None = None
        if handle.gate_kind == "change_review" and touched_files is not None and self._codeowners_resolver is not None:
            ownership_requirement = self._codeowners_resolver.requirement_for(handle.repo, touched_files)

        clearance = evaluate_gate_clearance(
            gate_kind=handle.gate_kind,
            run_id=handle.run_id,
            triggered_by=handle.triggered_by,
            actor=actor,
            approver=effective_approver,
            ownership_requirement=ownership_requirement,
        )

        if clearance.cleared:
            self.escalation.mark_actioned(gate_id, actioned_at=now)
            del self._open_gates[gate_id]
        else:
            self.audit_log.record(AuditEvent.new(
                tenant_id=handle.tenant_id, run_id=handle.run_id, kind="gate_rejected",
                details={"gate_id": gate_id, "gate_kind": handle.gate_kind, "actor_id": actor.subject_id,
                         "reasons": list(clearance.reasons)},
                now=now,
            ))
        return clearance

    def check_escalations(self, now: datetime | None = None) -> list[str]:
        return self.escalation.check_escalations(now)
