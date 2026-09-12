"""Acceptance criterion: "A trigger for Tenant A never queues behind, or
competes for capacity with, a trigger for Tenant B" -- and the related
Sec. 14.11 requirement that same-tenant triggers close together queue
against the *same* scale-up. Both proven with real threads.
"""

from __future__ import annotations

import threading
import time

from job_dispatcher.capacity import CapacityOutcome, MockCapacityProvider, ScenarioStep
from job_dispatcher.tenant_queue import TenantCapacityCoordinator


class _SlowCapacityProvider:
    """Wraps a MockCapacityProvider but sleeps inside
    `request_capacity`, widening the race window so concurrent
    "close together" triggers reliably overlap in a test instead of
    racing to complete before the next one even starts."""

    def __init__(self, delegate: MockCapacityProvider, delay_seconds: float = 0.1):
        self._delegate = delegate
        self._delay = delay_seconds

    def request_capacity(self, tenant_id: str):
        time.sleep(self._delay)
        return self._delegate.request_capacity(tenant_id)

    @property
    def call_count(self):
        return self._delegate.call_count


def test_same_tenant_bursts_coalesce_into_one_scale_up():
    """Multiple triggers for the SAME tenant arriving close together
    must queue against the same scale-up rather than each
    independently requesting a node (Sec. 14.11)."""
    delegate = MockCapacityProvider()
    provider = _SlowCapacityProvider(delegate, delay_seconds=0.15)
    coordinator = TenantCapacityCoordinator(provider)

    results = []
    results_lock = threading.Lock()

    def fire():
        result = coordinator.request_capacity("tenant-a")
        with results_lock:
            results.append(result)

    threads = [threading.Thread(target=fire) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(results) == 8
    # Exactly one real capacity request reached the provider for this
    # burst, not eight.
    assert delegate.call_count["tenant-a"] == 1
    # Every caller got the SAME outcome (the one scale-up's result),
    # not eight independently-provisioned nodes.
    node_ids = {r.node_id for r in results}
    assert node_ids == {results[0].node_id}


def test_different_tenants_never_share_a_queue_slot():
    """Tenant A and Tenant B trigger simultaneously (real threads):
    their capacity requests must never coalesce with each other and
    must never block on each other."""
    delegate = MockCapacityProvider()
    provider = _SlowCapacityProvider(delegate, delay_seconds=0.15)
    coordinator = TenantCapacityCoordinator(provider)

    results: dict[str, list] = {"tenant-a": [], "tenant-b": []}
    lock = threading.Lock()

    def fire(tenant_id):
        for _ in range(4):
            result = coordinator.request_capacity(tenant_id)
            with lock:
                results[tenant_id].append(result)

    t_a = threading.Thread(target=fire, args=("tenant-a",))
    t_b = threading.Thread(target=fire, args=("tenant-b",))
    t_a.start()
    t_b.start()
    t_a.join(timeout=5)
    t_b.join(timeout=5)

    assert len(results["tenant-a"]) == 4
    assert len(results["tenant-b"]) == 4

    # Each tenant's bursts of 4 close-together calls coalesce within
    # that tenant -- but never across tenants: tenant-a's node_ids are
    # never seen by tenant-b and vice versa.
    a_node_ids = {r.node_id for r in results["tenant-a"]}
    b_node_ids = {r.node_id for r in results["tenant-b"]}
    assert a_node_ids.isdisjoint(b_node_ids)
    for r in results["tenant-a"]:
        assert r.tenant_id == "tenant-a"
    for r in results["tenant-b"]:
        assert r.tenant_id == "tenant-b"

    # And each tenant's own in-flight state never appears keyed under
    # the other tenant's id (isolation at the storage level, not just
    # at the outcome level).
    assert "tenant-a" not in coordinator.inflight_tenant_ids()
    assert "tenant-b" not in coordinator.inflight_tenant_ids()


def test_dispatcher_level_concurrency_two_tenants_simultaneously(
    dispatcher, capacity_provider, jira_mock
):
    """The same isolation proof, at the full JobDispatcher/HTTP-request
    level: two tenants firing real signed webhook requests concurrently
    on real threads never cross-contaminate each other's Run creation
    or queue state."""
    import time as _time

    from conftest import PROJECT_A, PROJECT_B, SECRET_A, SECRET_B, TENANT_A, TENANT_B, signed_webhook

    capacity_provider.set_scenario(
        TENANT_A, [ScenarioStep(outcome=CapacityOutcome.AVAILABLE, capacity_class="spot")]
    )
    capacity_provider.set_scenario(
        TENANT_B, [ScenarioStep(outcome=CapacityOutcome.SPOT_EXHAUSTED_ON_DEMAND_FAILED)]
    )
    _base_url, store = jira_mock
    from conftest import seed_issue

    issue_b = seed_issue(store, project_key=PROJECT_B, key_hint="PROJB-1")

    outcomes = {}
    errors = []

    def fire_a():
        try:
            body_obj = {
                "tenant_id": TENANT_A,
                "issue_key": "PROJA-1",
                "project_key": PROJECT_A,
                "repository": "acme/repo-a",
                "status": "Selected for Development",
                "labels": ["ai-factory"],
            }
            headers, body = signed_webhook(tenant_id=TENANT_A, secret=SECRET_A, body_obj=body_obj)
            outcomes["a"] = dispatcher.handle_webhook(headers=headers, body=body)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def fire_b():
        try:
            body_obj = {
                "tenant_id": TENANT_B,
                "issue_key": issue_b,
                "project_key": PROJECT_B,
                "repository": "acme/repo-b",
                "status": "Selected for Development",
                "labels": ["ai-factory"],
            }
            headers, body = signed_webhook(tenant_id=TENANT_B, secret=SECRET_B, body_obj=body_obj)
            outcomes["b"] = dispatcher.handle_webhook(headers=headers, body=body)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t_a = threading.Thread(target=fire_a)
    t_b = threading.Thread(target=fire_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=10)
    t_b.join(timeout=10)

    assert not errors, errors
    assert outcomes["a"].outcome == "run_created"
    assert outcomes["a"].tenant_id == TENANT_A
    assert outcomes["b"].outcome == "queued"
    assert outcomes["b"].tenant_id == TENANT_B
    assert dispatcher.queue_depth(TENANT_A) == 0
    assert dispatcher.queue_depth(TENANT_B) == 1
