"""JobDispatcher: ties the Sec. 17.3 auth gate, Sec. 4.4 tenant
resolution, Sec. 14.11 capacity request/queueing, F2 Run creation, and
the capacity-unavailable Jira comment together into the single
end-to-end path a webhook request takes.

Call order is enforced by the types each step consumes, not by
convention:

    headers/body
        -> webhook_auth.require_authenticated   -> AuthenticatedTrigger
        -> tenant_resolution.require_resolved    -> ResolvedTrigger
        -> tenant_queue.TenantCapacityCoordinator -> CapacityResult
        -> run_registry.RegistryService.create_run   (capacity available)
           OR
           tenant_queue.TenantTriggerQueue + issue_tracker JiraClient.post_comment
               (capacity unavailable -- Sec. 14.11: "queued, not dropped")

D1 explicitly does NOT execute the agent loop (Sec. 14.11, this
package's brief): once `create_run` succeeds the Run Registry shows the
story at the `intake` stage, ready for D2's orchestrator to pick up
inside the now-provisioned cell. Nothing past `create_run` is this
package's job.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from issue_tracker.jira_client import JiraClient
from run_registry import RegistryService
from run_registry.result import Outcome as RegistryOutcome

from job_dispatcher.capacity import CapacityOutcome, CapacityResult
from job_dispatcher.tenant_queue import TenantCapacityCoordinator, TenantTriggerQueue
from job_dispatcher.tenant_resolution import ResolvedTrigger, TenantDirectory, require_resolved
from job_dispatcher.webhook_auth import require_authenticated

_REASON_TEXT: dict[CapacityOutcome, str] = {
    CapacityOutcome.SPOT_EXHAUSTED_ON_DEMAND_FAILED: (
        "spot capacity is exhausted for your compute cell and the on-demand fallback also failed"
    ),
    CapacityOutcome.MAX_CONCURRENT_LIMIT_HIT: (
        "your tenant's configured maximum concurrent-job limit is currently reached"
    ),
}


def default_branch_for_issue(jira_key: str) -> str:
    """Deterministic target-branch naming for a triggered Run. The
    master spec pins branch/worktree-per-session as a *requirement*
    (Sec. 8.1) but does not pin a naming convention for it -- this is a
    documented D1 choice, not a master-spec quote; see this deliverable's
    final report for the "spec ambiguity" note. Overridable via
    `JobDispatcher(branch_for_issue=...)`.
    """
    return f"ai-factory/{jira_key.lower()}"


def default_trace_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class DispatchResult:
    """What handling one webhook request produced."""

    outcome: str  # "run_created" | "queued"
    tenant_id: str
    jira_key: str
    run: object | None = None  # run_registry.models.Run, when outcome == "run_created"
    queue_depth: int | None = None
    capacity_outcome: CapacityOutcome | None = None
    comment_posted: bool | None = None


class DispatchError(Exception):
    """A Run-creation failure the Registry Service itself reported
    (e.g. invalid input) -- distinct from an auth/resolution rejection,
    which is a different exception type raised earlier in the chain."""


class JobDispatcher:
    def __init__(
        self,
        *,
        secret_lookup: Callable[[str], bytes | None],
        tenant_directory: TenantDirectory,
        registry: RegistryService,
        capacity_provider,
        jira_client_for_tenant: Callable[[str], JiraClient],
        coordinator: TenantCapacityCoordinator | None = None,
        trigger_queue: TenantTriggerQueue[ResolvedTrigger] | None = None,
        branch_for_issue: Callable[[str], str] = default_branch_for_issue,
        trace_id_factory: Callable[[], str] = default_trace_id,
        observability: Any | None = None,
    ):
        self._secret_lookup = secret_lookup
        self._tenant_directory = tenant_directory
        self._registry = registry
        # D11 instrumentation (purely additive, defaults to None): an
        # `observability.ObservabilityClient`-shaped object. When
        # supplied, `_create_run` and `_queue_and_notify` below each ship
        # a real, off-node-first trace event for the webhook/queue path
        # (Section 16.4), tagged with the same `trace_id` this dispatcher
        # already mints/threads through `RegistryService.create_run` --
        # the earliest point in the whole System that trace_id exists.
        # Every existing call site that constructs a `JobDispatcher`
        # without this argument is completely unaffected.
        self._observability = observability
        # Each JobDispatcher instance owns its own coordinator/queue by
        # default -- deliberately so: this is what makes the
        # multi-replica shortcut visible/testable (two JobDispatcher
        # instances = two independent in-process coordinators, exactly
        # as two replicas would each get their own). Passing the same
        # coordinator/queue into two instances simulates a shared store
        # instead; see tests/test_multi_replica_shortcut.py.
        self._coordinator = coordinator or TenantCapacityCoordinator(capacity_provider)
        self._trigger_queue = trigger_queue or TenantTriggerQueue()
        self._jira_client_for_tenant = jira_client_for_tenant
        self._branch_for_issue = branch_for_issue
        self._trace_id_factory = trace_id_factory

    # ------------------------------------------------------------------
    # the single entry point every external trigger takes
    # ------------------------------------------------------------------
    def handle_webhook(self, *, headers: dict[str, str], body: bytes, now: float | None = None) -> DispatchResult:
        # Step 1 (Sec. 17.3): auth, before anything else can run at all.
        # `require_authenticated` either returns an AuthenticatedTrigger
        # or raises -- there is no way to obtain a value usable by step
        # 2 without passing this first.
        trigger = require_authenticated(headers=headers, body=body, secret_lookup=self._secret_lookup, now=now)

        # Step 2 (Sec. 4.4): tenant resolution. Only reachable with an
        # AuthenticatedTrigger in hand.
        resolved = require_resolved(trigger=trigger, directory=self._tenant_directory)

        return self._dispatch(resolved)

    # ------------------------------------------------------------------
    # draining a tenant's queued triggers once capacity frees up
    # (a real deployment wires this to D6's capacity-available signal;
    # here it is a plain method a caller/test invokes directly)
    # ------------------------------------------------------------------
    def retry_queued(self, tenant_id: str) -> DispatchResult | None:
        item = self._trigger_queue.retry_next(tenant_id)
        if item is None:
            return None
        return self._dispatch(item)

    def queue_depth(self, tenant_id: str) -> int:
        return self._trigger_queue.depth(tenant_id)

    # ------------------------------------------------------------------
    # internal: capacity request + Run creation OR queue + Jira comment
    # ------------------------------------------------------------------
    def _dispatch(self, resolved: ResolvedTrigger) -> DispatchResult:
        # Step 3 (Sec. 14.11): request capacity from THIS tenant's own
        # pool only -- coalesced against any in-flight request for the
        # same tenant_id.
        capacity_result: CapacityResult = self._coordinator.request_capacity(resolved.tenant_id)

        if capacity_result.is_available:
            return self._create_run(resolved, capacity_result)

        return self._queue_and_notify(resolved, capacity_result)

    def _create_run(self, resolved: ResolvedTrigger, capacity_result: CapacityResult) -> DispatchResult:
        assert capacity_result.capacity_class is not None
        run_result = self._registry.create_run(
            tenant_id=resolved.tenant_id,
            jira_key=resolved.jira_key,
            repo=resolved.repository,
            branch=self._branch_for_issue(resolved.jira_key),
            capacity_class=capacity_result.capacity_class,
            trace_id=self._trace_id_factory(),
        )
        if run_result.outcome != RegistryOutcome.OK:
            raise DispatchError(f"RegistryService.create_run failed: {run_result}")

        if self._observability is not None:
            self._observability.record_stage_transition(
                trace_id=run_result.data.trace_id,
                run_id=run_result.data.id,
                tenant_id=resolved.tenant_id,
                from_stage="none",
                to_stage=run_result.data.stage,
                source="job_dispatcher_webhook",
            )

        return DispatchResult(
            outcome="run_created",
            tenant_id=resolved.tenant_id,
            jira_key=resolved.jira_key,
            run=run_result.data,
            capacity_outcome=capacity_result.outcome,
        )

    def _queue_and_notify(self, resolved: ResolvedTrigger, capacity_result: CapacityResult) -> DispatchResult:
        # Sec. 14.11: "queued, not dropped."
        self._trigger_queue.enqueue(resolved.tenant_id, resolved, reason=capacity_result.outcome.value)
        depth = self._trigger_queue.depth(resolved.tenant_id)

        # Sec. 14.11: "the Jira issue receives an automatic comment
        # stating the delay and the reason." Posted through D4's real,
        # tested `JiraClient.post_comment` -- never silence.
        comment_posted = self._post_delay_comment(resolved, capacity_result)

        if self._observability is not None:
            # No Run exists yet for a queued trigger (capacity wasn't
            # available), so there is no F2 trace_id to attach to yet --
            # this event is tagged by tenant_id/jira_key only, and joins
            # up with the eventual run-creation event by jira_key once
            # `retry_next` succeeds.
            self._observability.sink.emit(
                kind="log",
                name="webhook_queued",
                run_id=None,
                tenant_id=resolved.tenant_id,
                attributes={
                    "jira_key": resolved.jira_key,
                    "reason": capacity_result.outcome.value,
                    "queue_depth": depth,
                    "comment_posted": comment_posted,
                },
            )

        return DispatchResult(
            outcome="queued",
            tenant_id=resolved.tenant_id,
            jira_key=resolved.jira_key,
            queue_depth=depth,
            capacity_outcome=capacity_result.outcome,
            comment_posted=comment_posted,
        )

    def _post_delay_comment(self, resolved: ResolvedTrigger, capacity_result: CapacityResult) -> bool:
        client = self._jira_client_for_tenant(resolved.tenant_id)
        reason = _REASON_TEXT.get(capacity_result.outcome, capacity_result.outcome.value)
        detail = f" ({capacity_result.detail})" if capacity_result.detail else ""
        body = (
            "The factory received this trigger but could not start immediately: "
            f"{reason}{detail}. The request has been queued and will be retried automatically "
            "once capacity frees up -- this is not a failure, and no action is needed unless "
            "the delay is unexpectedly long."
        )
        result = client.post_comment(issue_key=resolved.jira_key, body=body, comment_type="capacity-delay")
        return result.get("outcome") == "ok"
