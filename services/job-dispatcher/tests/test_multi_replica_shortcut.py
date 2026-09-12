"""Acceptance criterion: "Dispatcher runs multi-replica; killing one
replica doesn't drop an in-flight trigger" -- and the brief's explicit
instruction: since there is no real load balancer/multi-process
deployment to test here, prove the *design* has no single-process-only
state, or say so explicitly if a shortcut was taken.

We took the shortcut: `TenantCapacityCoordinator`/`TenantTriggerQueue`
hold their state as plain in-process dicts (see `tenant_queue.py`'s
module docstring). This test makes that shortcut's consequence
concrete and visible rather than silently claiming multi-replica
safety.

Two things this test demonstrates:

  1. WITHIN one process (one JobDispatcher/coordinator instance), a
     same-tenant burst correctly coalesces into one scale-up --
     `test_tenant_queue_isolation.py` already proves this; not
     repeated here.

  2. ACROSS two independent JobDispatcher instances -- exactly what two
     load-balanced replicas of this service, each started fresh in
     its own process, would be -- that same coalescing guarantee is
     LOST: each replica's in-process coordinator thinks it is the sole
     in-flight requester for a tenant it knows nothing about the other
     replica already handling. A burst that should produce one
     scale-up per tenant produces one PER REPLICA it lands on instead.

This is the concrete, measurable gap a real multi-replica deployment
must close -- e.g. a Redis-backed distributed lock/dedup keyed by
tenant_id, or routing the coalescing decision through F2's Run Registry
itself (a control-plane resource every replica already reaches, per
Sec. 14.12) instead of an in-process dict. Nothing else in this
package needs to change to adopt either fix: both classes' public
methods (`request_capacity`, `enqueue`/`retry_next`) are the seam a
shared-store-backed implementation would replace.
"""

from __future__ import annotations

import threading

from job_dispatcher.capacity import MockCapacityProvider
from job_dispatcher.tenant_queue import TenantCapacityCoordinator


class _SlowCapacityProvider:
    """Adds a small delay inside `request_capacity` so two
    near-simultaneous callers reliably overlap the in-flight window
    instead of racing to complete before the next one even starts --
    same technique `test_tenant_queue_isolation.py` uses."""

    def __init__(self, delegate: MockCapacityProvider, delay_seconds: float = 0.15):
        self._delegate = delegate
        self._delay = delay_seconds

    def request_capacity(self, tenant_id: str):
        import time as _time

        _time.sleep(self._delay)
        return self._delegate.request_capacity(tenant_id)

    @property
    def call_count(self):
        return self._delegate.call_count


def test_two_independent_replicas_do_not_coalesce_a_shared_tenants_burst():
    """Simulates two replicas: two independent `TenantCapacityCoordinator`
    instances, each wrapping the SAME underlying capacity backend (the
    one part of the real architecture that genuinely is shared -- the
    tenant's actual node pool/Karpenter -- Sec. 14.13). If the
    *coordination* state were also shared (as a real multi-replica
    deployment needs), a burst for one tenant landing on both replicas
    would still coalesce to one real capacity request. It does not,
    with today's in-process-only shortcut.
    """
    delegate = MockCapacityProvider()
    shared_backend = _SlowCapacityProvider(delegate)

    replica_1 = TenantCapacityCoordinator(shared_backend)
    replica_2 = TenantCapacityCoordinator(shared_backend)

    barrier = threading.Barrier(2)

    def fire(coordinator: TenantCapacityCoordinator):
        barrier.wait(timeout=5)  # maximize the chance both requests are truly concurrent
        return coordinator.request_capacity("tenant-a")

    results = {}

    def run(name, coordinator):
        results[name] = fire(coordinator)

    t1 = threading.Thread(target=run, args=("replica_1", replica_1))
    t2 = threading.Thread(target=run, args=("replica_2", replica_2))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    # The documented gap: TWO real capacity requests reached the
    # shared backend for what should have been one coalesced scale-up,
    # because each replica's coordinator only knows about its own
    # in-process state.
    assert delegate.call_count["tenant-a"] == 2, (
        "this assertion documents the KNOWN GAP of the in-memory-only shortcut: "
        "two independent replicas do not coalesce a same-tenant burst between "
        "them. A real deployment needs this coordination state in a shared "
        "store (e.g. Redis, or a Run-Registry-backed lock) instead of each "
        "replica's own process memory."
    )
    # Each replica still internally behaves correctly in isolation --
    # it is cross-replica sharing that is the gap, not per-replica logic.
    assert results["replica_1"].outcome.value == "available"
    assert results["replica_2"].outcome.value == "available"


def test_single_process_coalescing_is_the_baseline_this_gap_is_measured_against():
    """Contrast case: ONE coordinator instance (one replica) handling
    the identical burst correctly coalesces to a single request -- the
    same scenario as the test above, differing only in whether the
    coordination state is shared (single instance) or not (two
    instances). This is what makes the gap above a replica-count
    problem specifically, not a bug in the coalescing logic itself.
    """
    delegate = MockCapacityProvider()
    backend = _SlowCapacityProvider(delegate)
    coordinator = TenantCapacityCoordinator(backend)

    barrier = threading.Barrier(2)

    def fire():
        barrier.wait(timeout=5)
        return coordinator.request_capacity("tenant-a")

    results = []
    results_lock = threading.Lock()

    def run():
        r = fire()
        with results_lock:
            results.append(r)

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert delegate.call_count["tenant-a"] == 1
    assert len(results) == 2
