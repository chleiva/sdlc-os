"""Per-tenant queueing (master spec Sec. 14.11):

    "Multiple qualifying triggers for the same tenant arriving close
    together queue against the same scale-up rather than each
    independently requesting a node, so a burst of stories does not
    multiply cost by the burst size; triggers from different tenants
    never queue against each other, since they are never requesting
    the same pool."

Two distinct queueing behaviors live here, both keyed strictly by
tenant_id so two tenants' state can never share a slot:

1. `TenantCapacityCoordinator` -- in-flight request coalescing. If a
   capacity request for tenant A is already outstanding when a second
   trigger for tenant A arrives, the second trigger *joins* the first's
   outcome instead of issuing its own `CapacityProvider.request_capacity`
   call. This is what makes a burst of triggers for one tenant result in
   one scale-up, not N.

2. `TenantTriggerQueue` -- what happens when capacity comes back
   unavailable (Sec. 14.11: "queued, not dropped"). The trigger is held
   here, keyed by tenant_id, until something (an operator action, or a
   real deployment's capacity-became-available callback from D6) drains
   it via `retry_next`.

IMPORTANT -- multi-replica note (see the brief's "Multi-replica safety"
requirement and `tests/test_multi_replica_shortcut.py`): both classes
below hold their state as **plain Python dicts in this process's
memory**. That is a deliberate, explicitly-documented shortcut for this
pass, not a claim that this design is multi-replica-safe. Run
`services/job-dispatcher/tests/test_multi_replica_shortcut.py` to see
the concrete failure mode: two independent replicas (two independent
`TenantCapacityCoordinator` instances, exactly as ordinary
load-balanced horizontal scaling would produce) each believe they are
the sole in-flight request for the same tenant, so a burst that would
correctly coalesce into one scale-up *within* a single replica produces
one scale-up **per replica** it happens to land on. A real multi-replica
deployment needs this coordination state in a shared store all replicas
see -- e.g. a Redis-backed distributed lock/dedup key per tenant_id, or
(reusing infrastructure this system already has) a row in F2's Run
Registry itself (Sec. 14.12) that every replica reads/writes through the
Registry Service rather than an in-process dict. Swapping the storage
this module uses for that shared store, without changing
`dispatcher.py`'s call sites, is the intended follow-up -- not scoped to
D1 (its brief is explicit that provisioning/infra for a real multi-replica
deployment is out of scope), documented here so it is not silently
mistaken for already being handled.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Generic, TypeVar

from job_dispatcher.capacity import CapacityProvider, CapacityResult

T = TypeVar("T")


class TenantCapacityCoordinator:
    """Coalesces concurrent `request_capacity` calls for the same
    tenant into a single call to the underlying `CapacityProvider`.

    Per-tenant only: a `_PendingRequest` is stored under its tenant_id
    key and no other tenant's call ever reads or waits on it -- see
    `tests/test_tenant_queue_isolation.py` for the concurrency proof.
    """

    def __init__(self, provider: CapacityProvider):
        self._provider = provider
        self._lock = threading.Lock()
        self._inflight: dict[str, "_PendingRequest"] = {}

    def request_capacity(self, tenant_id: str) -> CapacityResult:
        with self._lock:
            pending = self._inflight.get(tenant_id)
            if pending is not None:
                is_leader = False
            else:
                pending = _PendingRequest()
                self._inflight[tenant_id] = pending
                is_leader = True

        if not is_leader:
            # Join the scale-up already in flight for THIS tenant only
            # -- `pending` was looked up strictly by this tenant_id key,
            # so a different tenant's in-flight request is never even
            # visible here, let alone waited on.
            return pending.wait()

        try:
            result = self._provider.request_capacity(tenant_id)
        except BaseException as exc:  # noqa: BLE001 - must still release followers
            with self._lock:
                self._inflight.pop(tenant_id, None)
            pending.set_exception(exc)
            raise
        else:
            with self._lock:
                self._inflight.pop(tenant_id, None)
            pending.set_result(result)
            return result

    def inflight_tenant_ids(self) -> frozenset[str]:
        """Test/introspection helper: which tenants currently have an
        outstanding coalesced request in this process."""
        with self._lock:
            return frozenset(self._inflight.keys())


class _PendingRequest:
    """A one-shot result future, shared by every follower waiting on
    the same in-flight capacity request."""

    def __init__(self) -> None:
        self._event = threading.Event()
        self._result: CapacityResult | None = None
        self._exception: BaseException | None = None

    def set_result(self, result: CapacityResult) -> None:
        self._result = result
        self._event.set()

    def set_exception(self, exc: BaseException) -> None:
        self._exception = exc
        self._event.set()

    def wait(self) -> CapacityResult:
        self._event.wait()
        if self._exception is not None:
            raise self._exception
        assert self._result is not None
        return self._result


@dataclass(frozen=True)
class QueuedTrigger(Generic[T]):
    tenant_id: str
    item: T
    reason: str


class TenantTriggerQueue(Generic[T]):
    """Holds triggers that could not get capacity immediately (Sec.
    14.11: "queued, not dropped"), strictly partitioned by tenant_id --
    a `deque` per tenant, never one shared deque triggers from
    different tenants could interleave in.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: dict[str, deque] = {}

    def enqueue(self, tenant_id: str, item: T, *, reason: str) -> QueuedTrigger[T]:
        with self._lock:
            queue = self._queues.setdefault(tenant_id, deque())
            queue.append(item)
        return QueuedTrigger(tenant_id=tenant_id, item=item, reason=reason)

    def depth(self, tenant_id: str) -> int:
        with self._lock:
            queue = self._queues.get(tenant_id)
            return len(queue) if queue else 0

    def retry_next(self, tenant_id: str) -> T | None:
        """Pop the oldest queued trigger for `tenant_id` (FIFO), for a
        caller (an operator, or a real deployment's
        capacity-freed-up callback) to re-attempt. Returns None if
        nothing is queued for this tenant. Never touches any other
        tenant's queue.
        """
        with self._lock:
            queue = self._queues.get(tenant_id)
            if not queue:
                return None
            return queue.popleft()

    def all_tenant_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(t for t, q in self._queues.items() if q)
