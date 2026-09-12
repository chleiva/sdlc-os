"""Tenant resolution from the Jira project/workspace mapping (Sec. 4.4,
Sec. 14.11: "resolves which tenant the trigger belongs to (Section 4.4's
Jira project/workspace mapping...) -- every capacity request from this
point on is a request for that tenant's own compute cell").

This step only ever runs on an already-`AuthenticatedTrigger` (see
`webhook_auth.py`) -- there is no function here that accepts raw
headers/body, so it cannot be reached by an unauthenticated request.

Resolution is deliberately independent of the header-carried
`claimed_tenant_id` used to pick the HMAC key: the header is documented
(webhook_signing.py) as "never trusted for anything except selecting
which key to check the signature against". Here we instead look up
`project_key` (carried in the signed body) against a configured
Jira-project-to-tenant mapping -- the actual Sec. 4.4 mapping -- and
require it to agree with the header's claim. A mismatch is treated as a
hard rejection (defense in depth: even a validly-signed request must
still name a project that maps back to the tenant whose key signed it).
"""

from __future__ import annotations

from dataclasses import dataclass

from job_dispatcher.webhook_auth import AuthenticatedTrigger


@dataclass(frozen=True)
class ResolvedTrigger:
    """Proof that a specific `AuthenticatedTrigger` was successfully
    resolved to one tenant. The only way to obtain one is
    `resolve_tenant`/`require_resolved` -- there is no other
    constructor. Every function past this point (capacity requests,
    Run creation) takes a `ResolvedTrigger`, not a bare tenant_id
    string, so tenant resolution cannot be skipped or forged.
    """

    tenant_id: str
    jira_key: str
    project_key: str
    repository: str
    status: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class UnresolvedTrigger:
    reason: str


class TenantResolutionError(Exception):
    """Raised by `require_resolved` when a trigger cannot be resolved to
    exactly one tenant (unknown project, or project/tenant mismatch)."""

    def __init__(self, unresolved: UnresolvedTrigger):
        super().__init__(unresolved.reason)
        self.unresolved = unresolved


class TenantDirectory:
    """The configured Jira-project-to-tenant mapping (Sec. 4.4). A
    simple, explicit config -- a dict loaded from JSON at startup is
    enough per the brief ("a simple config file is fine"); nothing here
    requires a database.
    """

    def __init__(self, project_to_tenant: dict[str, str]):
        self._project_to_tenant = dict(project_to_tenant)

    @classmethod
    def from_mapping(cls, mapping: dict[str, str]) -> "TenantDirectory":
        return cls(mapping)

    @classmethod
    def load_json(cls, path: str) -> "TenantDirectory":
        import json

        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        mapping = raw.get("project_to_tenant", raw)
        return cls(mapping)

    def tenant_for_project(self, project_key: str) -> str | None:
        return self._project_to_tenant.get(project_key)


def resolve_tenant(
    *, trigger: AuthenticatedTrigger, directory: TenantDirectory
) -> ResolvedTrigger | UnresolvedTrigger:
    """The single function capable of turning an `AuthenticatedTrigger`
    into a `ResolvedTrigger`. Requires the fields D4's `webhook_relay`
    signs into the body: tenant_id, issue_key, project_key, repository,
    status, labels (see `services/issue-tracker/src/issue_tracker/webhook_relay.py`'s
    `SignedDispatch.body_json`, the contract this mirrors).
    """
    try:
        payload = trigger.body_json()
    except Exception as exc:  # noqa: BLE001 - malformed body is a resolution failure, not a crash
        return UnresolvedTrigger(reason=f"request body is not valid JSON: {exc}")

    missing = [f for f in ("issue_key", "project_key", "repository") if not payload.get(f)]
    if missing:
        return UnresolvedTrigger(reason=f"request body missing required field(s): {missing}")

    project_key = payload["project_key"]
    resolved_tenant_id = directory.tenant_for_project(project_key)
    if resolved_tenant_id is None:
        return UnresolvedTrigger(
            reason=f"project {project_key!r} is not mapped to any tenant in the configured directory"
        )

    if resolved_tenant_id != trigger.claimed_tenant_id:
        # The HMAC-authenticated header claimed one tenant; the
        # configured project->tenant mapping resolves this project to a
        # different one. Fail closed rather than trust either alone.
        return UnresolvedTrigger(
            reason=(
                f"project {project_key!r} resolves to tenant {resolved_tenant_id!r}, which does not match "
                f"the authenticated request's claimed tenant {trigger.claimed_tenant_id!r}"
            )
        )

    return ResolvedTrigger(
        tenant_id=resolved_tenant_id,
        jira_key=payload["issue_key"],
        project_key=project_key,
        repository=payload["repository"],
        status=payload.get("status", ""),
        labels=tuple(payload.get("labels", [])),
    )


def require_resolved(*, trigger: AuthenticatedTrigger, directory: TenantDirectory) -> ResolvedTrigger:
    outcome = resolve_tenant(trigger=trigger, directory=directory)
    if isinstance(outcome, UnresolvedTrigger):
        raise TenantResolutionError(outcome)
    return outcome
