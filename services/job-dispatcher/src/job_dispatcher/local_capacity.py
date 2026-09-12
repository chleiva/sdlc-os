"""The local, single-tenant Docker Compose deployment mode's capacity
provider (master spec Sec. 14.16, "Alternative Deployment Mode: Local,
Single-Tenant Docker Compose", New Rev 9):

    "the job dispatcher's capacity-request step (Section 14.11) is
    satisfied by a trivial, always-available local capacity provider
    rather than a real GPU-provisioning request, since there is no GPU
    capacity to provision or wait for in this mode."

and D1's brief (New, Rev 9): "In the Docker Compose deployment mode
(Sec. 14.16), that provider is a trivial, always-available local
implementation of the same interface -- there is no GPU to provision or
wait for, since inference is delegated to an external API-key vendor
(Sec. 13.8) -- so a capacity request in this mode always succeeds
immediately rather than triggering real provisioning."

This is `capacity.py`'s `CapacityProvider` interface, implemented for
real -- not a test double. `capacity.py`'s `MockCapacityProvider` exists
purely to drive test *scenarios* (including deliberately simulating
unavailable/queued capacity); `LocalCapacityProvider` is production code
for the Compose deployment mode, and by design has no way to return
anything other than `CapacityOutcome.AVAILABLE` -- there is no
"unavailable" branch here to configure, because Sec. 14.16 says none
exists in this mode. Both `dispatcher.py` and `tenant_queue.py` accept
this exactly as they accept any other `CapacityProvider`; neither needed
(or received) any change to support it.

`capacity.py` itself is intentionally left unmodified -- this is a new,
sibling module, per D1's brief for adding the Compose-mode capacity
provider without touching the existing interface/mock definitions.
"""

from __future__ import annotations

import threading

from job_dispatcher.capacity import CapacityOutcome, CapacityResult

# Sec. 14.16: no GPU node pool, no spot-capacity strategy (Sec. 14.8
# does not apply) in this mode -- there is no real "capacity class" to
# report. F2's Run Registry `RegistryService.create_run` currently
# hard-validates `capacity_class` against a closed enum,
# `{"on_demand", "spot"}` (`run_registry/service.py`'s
# `_VALID_CAPACITY_CLASSES`), with no third value for a local/Compose
# grant -- one more instance of the schema gap this repo's CLAUDE.md
# "Known cross-deliverable gaps" section already tracks for F2 (no
# column/enum value dedicated to the Sec. 14.16 deployment mode).
# Rather than pass a value F2 would reject (or unilaterally widen F2's
# enum, out of scope for this directory), `LocalCapacityProvider`
# reports "on_demand" -- the closest existing semantic fit, since this
# mode explicitly has no spot-capacity strategy to speak of -- and this
# is flagged here for reconciliation if/when F2's schema grows a
# dedicated local-mode capacity class.
DEFAULT_CAPACITY_CLASS = "on_demand"
DEFAULT_NODE_ID = "local-compose-host"


class LocalCapacityProvider:
    """Always-available `CapacityProvider` for the Docker Compose
    deployment mode (Sec. 14.16).

    `request_capacity` never blocks, never queues, and never returns
    anything other than `CapacityOutcome.AVAILABLE` -- there is no GPU
    to provision or wait for in this mode (model inference is delegated
    to an external API-key vendor per Sec. 13.8, a separate
    deliverable), so every call succeeds immediately regardless of
    tenant_id or how many calls precede it.

    Thread-safe like every `CapacityProvider` must be (`capacity.py`'s
    protocol docstring): the only shared state is a per-tenant call
    counter, guarded by a lock, kept purely for test/introspection
    parity with `MockCapacityProvider.call_count` -- it plays no role in
    the (trivial, always-"yes") decision itself.
    """

    def __init__(
        self,
        *,
        tenant_id: str | None = None,
        capacity_class: str = DEFAULT_CAPACITY_CLASS,
        node_id: str = DEFAULT_NODE_ID,
    ):
        # `tenant_id`, when given, is an optional defensive assertion --
        # Sec. 14.16: this mode is single-tenant by construction, with
        # tenant_id fixed to one configured value. Passing it here lets
        # a caller catch a misconfiguration (a resolved trigger naming
        # some other tenant_id) loudly instead of silently granting
        # capacity to a tenant that should not exist in this deployment
        # mode. Leaving it unset (the default) accepts any tenant_id,
        # which is exactly what every existing test/contract for
        # `CapacityProvider` already assumes.
        self._tenant_id = tenant_id
        self._capacity_class = capacity_class
        self._node_id = node_id
        self._lock = threading.Lock()
        self.call_count: dict[str, int] = {}

    def request_capacity(self, tenant_id: str) -> CapacityResult:
        if self._tenant_id is not None and tenant_id != self._tenant_id:
            raise ValueError(
                f"LocalCapacityProvider is fixed to tenant_id {self._tenant_id!r} "
                f"(Sec. 14.16 single-tenant Docker Compose mode); got a request for "
                f"{tenant_id!r} instead -- this indicates a misconfiguration, not a "
                f"capacity-unavailable condition."
            )

        with self._lock:
            self.call_count[tenant_id] = self.call_count.get(tenant_id, 0) + 1

        # Always AVAILABLE, always immediately -- Sec. 14.16 has no
        # "unavailable" branch for this provider to express.
        return CapacityResult(
            outcome=CapacityOutcome.AVAILABLE,
            tenant_id=tenant_id,
            capacity_class=self._capacity_class,
            node_id=self._node_id,
        )
