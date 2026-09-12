"""The provisioning/orchestration client boundary -- THE MOCK BOUNDARY.

Everything above this module (`interruption_watcher.py`, `cold_start.py`,
the no-shared-node tests) is real, tested control logic. Everything this
module's `ProvisioningClient` Protocol describes -- actually cordoning a
Kubernetes node, calling a model-serving pod's drain/stop-accepting
endpoint, launching a new GPU node, polling its health -- requires a real
Kubernetes API server and a real cloud account, neither of which exists
in this environment (same hard constraint F1/D5 were built under; see
`tests/mock_github_server.py` in `services/source-control` for the same
pattern applied to a different boundary).

Stated plainly, the same way `services/issue-tracker/SETUP.md` states its
own mock boundary:

  * REAL: the *sequence* of calls D6's control logic makes (cordon, then
    stop-accepting, then drain, then flush, then checkpoint; launch, then
    poll health), the state machine driving that sequence
    (`interruption_watcher.py`), the timing/budget accounting
    (`clock.py`, `cold_start.py`), and everything each of those calls
    interacts with once inside the platform's own boundary (F2's real
    `RegistryService` for the checkpoint write).
  * MOCKED: what happens on the other side of `ProvisioningClient` --
    `FakeProvisioningClient` below is an in-memory simulation of a
    Kubernetes control plane + cloud API, standing in for what would, in
    a real deployment, be a `kubernetes` Python client talking to a real
    API server (`cordon` = patch a Node's `spec.unschedulable`; `drain`
    = evict pods respecting PodDisruptionBudgets; `request_node` = a
    cloud SDK call that creates an EC2/GCE/Azure instance or, in this
    repo's actual IaC layer, Karpenter reacting to a NodePool's demand)
    plus a cloud metadata-endpoint poll / EventBridge subscription for
    the interruption signal itself (spec Section 14.8). None of that
    wire protocol is real here -- only the shape of the calls a real
    implementation would need to make, and the order/timing discipline
    around them.

A real implementation swaps `FakeProvisioningClient` for one built on
`kubernetes.client` (cordon/drain) + the target cloud's SDK
(boto3/google-cloud-compute/azure-mgmt-compute) without changing anything
in `interruption_watcher.py` or `cold_start.py`, which depend only on the
`ProvisioningClient` Protocol below.
"""

from __future__ import annotations

import itertools
import threading
import uuid
from dataclasses import dataclass
from typing import Protocol

from tenant_cell.clock import Clock
from tenant_cell.naming import node_pool_name, tenant_environment


@dataclass(frozen=True)
class NodeHandle:
    """A provisioned (real or simulated) GPU node."""

    node_id: str
    tenant_id: str
    pool_name: str
    environment: str


@dataclass(frozen=True)
class DrainResult:
    """Outcome of step 3 of the interruption sequence (spec Section 14.8).

    `timed_out` is not a failure of the watcher: the spec explicitly
    allows in-flight requests to "complete or fail back to the
    orchestrator's own retry logic ... within the warning window" -- a
    timeout converts any still-in-flight requests into
    `failed_to_retry_count` rather than blocking the sequence.
    """

    drained_count: int
    failed_to_retry_count: int
    timed_out: bool


class ProvisioningClient(Protocol):
    """Everything D6's control logic needs from "a Kubernetes cluster and
    a cloud account" -- the mock boundary. See module docstring.
    """

    def request_node(self, *, tenant_id: str, base_environment: str) -> NodeHandle: ...

    def health_check(self, node: NodeHandle) -> bool: ...

    def cordon(self, node: NodeHandle) -> None: ...

    def stop_accepting_inference(self, node: NodeHandle) -> None: ...

    def drain(self, node: NodeHandle, *, budget_seconds: float) -> DrainResult: ...

    def flush_observability(self, node: NodeHandle) -> bool: ...


@dataclass
class _SimulatedNodeState:
    in_flight_requests: int = 0
    ready_after_seconds: float = 0.0
    requested_at: float = 0.0
    cordoned: bool = False
    accepting_inference: bool = True


class FakeProvisioningClient:
    """In-memory `ProvisioningClient` for tests -- see module docstring
    for exactly what is/isn't real about it.

    Deterministic and inspectable: every node it ever allocated is kept
    in `self.allocated_nodes` (node_id -> tenant_id) so a test can assert
    "no two different tenants were ever assigned the same node_id"
    directly against this client's own bookkeeping, not just against the
    handles a single call happened to return.
    """

    def __init__(self, clock: Clock, *, default_ready_after_seconds: float = 0.0):
        self._clock = clock
        self._default_ready_after_seconds = default_ready_after_seconds
        self._nodes: dict[str, _SimulatedNodeState] = {}
        self.allocated_nodes: dict[str, str] = {}  # node_id -> tenant_id
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def request_node(self, *, tenant_id: str, base_environment: str) -> NodeHandle:
        pool = node_pool_name(base_environment, tenant_id)
        env = tenant_environment(base_environment, tenant_id)
        # node_id is namespaced by the tenant-derived pool name plus a
        # per-call sequence number, so it is structurally impossible for
        # two different tenants (who always get two different pool
        # names, per naming.py) to ever collide on a node_id -- this
        # mirrors why real capacity-optimized-fleet allocation never
        # lands two tenants' instances in the same NodePool: they are
        # requested from different NodePool objects entirely. Guarded by
        # a lock so this holds under real concurrent calls (simulating
        # concurrent tenant provisioning requests), not just sequential
        # ones.
        with self._lock:
            node_id = f"i-{pool}-{next(self._seq):04d}-{uuid.uuid4().hex[:6]}"
            self._nodes[node_id] = _SimulatedNodeState(
                ready_after_seconds=self._default_ready_after_seconds,
                requested_at=self._clock.now(),
            )
            self.allocated_nodes[node_id] = tenant_id
        return NodeHandle(node_id=node_id, tenant_id=tenant_id, pool_name=pool, environment=env)

    def set_in_flight_requests(self, node: NodeHandle, count: int) -> None:
        """Test helper: simulate N in-flight inference requests on a node."""
        self._nodes[node.node_id].in_flight_requests = count

    def set_ready_after_seconds(self, node: NodeHandle, seconds: float) -> None:
        """Test helper: this node's health check won't pass until this
        many (simulated) seconds after `request_node` was called."""
        self._nodes[node.node_id].ready_after_seconds = seconds

    def health_check(self, node: NodeHandle) -> bool:
        state = self._nodes[node.node_id]
        return (self._clock.now() - state.requested_at) >= state.ready_after_seconds

    def cordon(self, node: NodeHandle) -> None:
        self._nodes[node.node_id].cordoned = True

    def stop_accepting_inference(self, node: NodeHandle) -> None:
        self._nodes[node.node_id].accepting_inference = False

    def drain(self, node: NodeHandle, *, budget_seconds: float) -> DrainResult:
        state = self._nodes[node.node_id]
        in_flight = state.in_flight_requests
        # Simulate each in-flight request taking a small, fixed amount of
        # (simulated) time to finish; whatever doesn't finish inside the
        # allotted budget fails back to orchestrator retry, per spec
        # ("let in-flight requests either complete or fail back to the
        # orchestrator's own retry logic ... within the warning window").
        per_request_seconds = 1.0
        max_drainable = int(budget_seconds // per_request_seconds) if budget_seconds > 0 else 0

        if in_flight <= max_drainable:
            elapsed = in_flight * per_request_seconds
            drained, failed_to_retry, timed_out = in_flight, 0, False
        else:
            elapsed = budget_seconds
            drained, failed_to_retry, timed_out = max_drainable, in_flight - max_drainable, True

        self._clock.sleep(max(0.0, elapsed))
        state.in_flight_requests = 0
        return DrainResult(
            drained_count=drained, failed_to_retry_count=failed_to_retry, timed_out=timed_out
        )

    def flush_observability(self, node: NodeHandle) -> bool:
        # Real implementation: force a Loki/Tempo/metrics-exporter flush
        # off-node before the reclaim lands (spec Section 14.8 step 4,
        # Section 16.4). Simulated here as instantaneous and always
        # successful; a test that needs to exercise a flush failure can
        # subclass and override this method.
        return True
