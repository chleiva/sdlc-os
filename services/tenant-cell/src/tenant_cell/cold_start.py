"""Cold-start budget tracking (spec Section 14.8 / Section 16.6).

Section 16.6 pins the target as "a few minutes" from node launch to
serving-ready, without a single exact figure -- **flagged for human
confirmation**: `DEFAULT_COLD_START_BUDGET_SECONDS` below is this
implementation's own reasonable placeholder (5 minutes), not a verbatim
spec number; a human should pin the real per-organization/per-tier
default (Section 19's configuration model) before this is load-bearing.

What this measures, stated plainly (there is no live node to launch in
this environment): the elapsed time between issuing a provisioning
request and the first successful health check *as seen by this
control-plane logic*, driven against `ProvisioningClient` (mocked --
see that module's docstring for the boundary). This is the control-logic
polling/orchestration overhead only -- it does not and cannot measure
real cloud instance boot time, AMI/userdata bootstrap, or real vLLM model
load time, none of which exist to measure here. A real deployment's
cold-start number is the sum of that real launch/boot/model-load latency
plus whatever small polling overhead this module adds; only the latter is
exercised by the tests in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass

from tenant_cell.clock import Clock
from tenant_cell.provisioning_client import NodeHandle, ProvisioningClient

# Section 16.6: "a few minutes" -- see module docstring for why this
# specific number is a placeholder pending human confirmation, not a
# verbatim spec figure.
DEFAULT_COLD_START_BUDGET_SECONDS = 5 * 60.0

DEFAULT_POLL_INTERVAL_SECONDS = 5.0
DEFAULT_MAX_POLLS = 200  # guards against an infinite loop on a health check that never passes


@dataclass(frozen=True)
class ColdStartResult:
    tenant_id: str
    node: NodeHandle
    launch_requested_at: float
    serving_ready_at: float | None
    poll_count: int
    budget_seconds: float

    @property
    def elapsed_seconds(self) -> float | None:
        if self.serving_ready_at is None:
            return None
        return self.serving_ready_at - self.launch_requested_at

    @property
    def within_budget(self) -> bool | None:
        elapsed = self.elapsed_seconds
        if elapsed is None:
            return None
        return elapsed <= self.budget_seconds

    @property
    def timed_out(self) -> bool:
        return self.serving_ready_at is None


def track_cold_start(
    client: ProvisioningClient,
    clock: Clock,
    *,
    tenant_id: str,
    base_environment: str,
    budget_seconds: float = DEFAULT_COLD_START_BUDGET_SECONDS,
    poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    max_polls: int = DEFAULT_MAX_POLLS,
) -> ColdStartResult:
    """Start timestamp at launch request; end timestamp at first
    successful health check. Polls `client.health_check` on the injected
    clock (no real sleeping) up to `max_polls` times, `poll_interval_seconds`
    apart, and reports whichever comes first: a passing health check, or
    exhausting `max_polls` (timed out -- `serving_ready_at` stays `None`).
    """
    node = client.request_node(tenant_id=tenant_id, base_environment=base_environment)
    launch_requested_at = clock.now()

    serving_ready_at: float | None = None
    poll_count = 0
    for poll_count in range(1, max_polls + 1):
        if client.health_check(node):
            serving_ready_at = clock.now()
            break
        clock.sleep(poll_interval_seconds)
    else:
        poll_count = max_polls

    return ColdStartResult(
        tenant_id=tenant_id,
        node=node,
        launch_requested_at=launch_requested_at,
        serving_ready_at=serving_ready_at,
        poll_count=poll_count,
        budget_seconds=budget_seconds,
    )
