"""The capacity-request integration point (master spec Sec. 14.11,
14.13): "requests a node from that tenant's own pool ... via Karpenter."

There is no real Karpenter/Kubernetes cluster available in this
environment (see D6's brief, "Explicitly not in scope: the job
dispatcher's *decision* to request capacity -- D1 requests capacity
through what D6 provisions"). This module defines the real interface D1
codes against -- `CapacityProvider` -- and a deterministic in-memory
implementation for tests. **D6 plugs a real implementation of this same
interface in** (one that actually calls Karpenter/the node-pool module
built by F1); nothing in `dispatcher.py` or `tenant_queue.py` needs to
change when that happens, since they only ever call `CapacityProvider`,
never a concrete provisioning mechanism.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class CapacityOutcome(str, Enum):
    AVAILABLE = "available"
    # Spot exhausted AND the on-demand fallback (Sec. 14.8) also failed.
    SPOT_EXHAUSTED_ON_DEMAND_FAILED = "spot-exhausted-on-demand-failed"
    # A configured maximum concurrent-job limit is already reached.
    MAX_CONCURRENT_LIMIT_HIT = "max-concurrent-limit-hit"


# The two outcomes Sec. 14.11 names as "no capacity available" --
# distinct reasons, but both mean "queue the trigger, don't drop it."
UNAVAILABLE_OUTCOMES = frozenset(
    {CapacityOutcome.SPOT_EXHAUSTED_ON_DEMAND_FAILED, CapacityOutcome.MAX_CONCURRENT_LIMIT_HIT}
)


@dataclass(frozen=True)
class CapacityResult:
    outcome: CapacityOutcome
    tenant_id: str
    # Populated only when outcome is AVAILABLE: which capacity class was
    # actually granted (Sec. 14.8) -- flows straight into F2's
    # `RegistryService.create_run(capacity_class=...)`.
    capacity_class: str | None = None
    node_id: str | None = None
    detail: str | None = None

    @property
    def is_available(self) -> bool:
        return self.outcome is CapacityOutcome.AVAILABLE


class CapacityProvider(Protocol):
    """The real interface D1 codes against. D6 provides the real
    implementation (calling Karpenter via F1's node-pool module,
    parameterized per tenant per Sec. 14.13); tests use
    `MockCapacityProvider` below. Both must be safe to call from
    multiple threads concurrently, since `tenant_queue.py` may invoke
    this from more than one in-flight request coordinator at once (one
    per tenant).
    """

    def request_capacity(self, tenant_id: str) -> CapacityResult:
        """Request a node from `tenant_id`'s own compute cell pool
        (never a pool shared with any other tenant). Blocks until the
        outcome is known (available / one of the unavailable
        outcomes) -- callers that want non-blocking queueing behavior
        get it from `tenant_queue.py`, not from this call itself.
        """
        ...


@dataclass
class ScenarioStep:
    """One canned outcome a `MockCapacityProvider` returns the Nth time
    `request_capacity` is called for a given tenant."""

    outcome: CapacityOutcome
    capacity_class: str | None = None
    node_id: str | None = None
    detail: str | None = None


class MockCapacityProvider:
    """Deterministic, in-memory stand-in for D6's real Karpenter-backed
    provider. Each tenant has its own configured sequence of outcomes
    (a queue of `ScenarioStep`s); each call to `request_capacity`
    consumes the next step for that tenant (the last configured step
    repeats forever once the queue is exhausted, so a test doesn't have
    to pre-script every call).

    Thread-safe (`threading.Lock`-guarded) and instrumented with a
    per-tenant call counter, which the multi-replica test
    (`test_multi_replica_shortcut.py`) uses to prove how many *actual*
    capacity requests reached this provider versus how many logical
    triggers asked for one.
    """

    def __init__(self, scenarios: dict[str, list[ScenarioStep]] | None = None):
        self._lock = threading.Lock()
        self._scenarios: dict[str, list[ScenarioStep]] = {
            k: list(v) for k, v in (scenarios or {}).items()
        }
        self._cursor: dict[str, int] = {}
        self.call_count: dict[str, int] = {}
        self._node_counter = 0

    def set_scenario(self, tenant_id: str, steps: list[ScenarioStep]) -> None:
        with self._lock:
            self._scenarios[tenant_id] = list(steps)
            self._cursor[tenant_id] = 0

    def request_capacity(self, tenant_id: str) -> CapacityResult:
        with self._lock:
            self.call_count[tenant_id] = self.call_count.get(tenant_id, 0) + 1
            steps = self._scenarios.get(tenant_id)
            if not steps:
                # Default: capacity is simply available, a fresh node.
                self._node_counter += 1
                return CapacityResult(
                    outcome=CapacityOutcome.AVAILABLE,
                    tenant_id=tenant_id,
                    capacity_class="spot",
                    node_id=f"node-{tenant_id}-{self._node_counter}",
                )
            idx = self._cursor.get(tenant_id, 0)
            step = steps[min(idx, len(steps) - 1)]
            self._cursor[tenant_id] = idx + 1

            if step.outcome is CapacityOutcome.AVAILABLE:
                self._node_counter += 1
                node_id = step.node_id or f"node-{tenant_id}-{self._node_counter}"
                return CapacityResult(
                    outcome=CapacityOutcome.AVAILABLE,
                    tenant_id=tenant_id,
                    capacity_class=step.capacity_class or "spot",
                    node_id=node_id,
                    detail=step.detail,
                )
            return CapacityResult(
                outcome=step.outcome,
                tenant_id=tenant_id,
                detail=step.detail,
            )
