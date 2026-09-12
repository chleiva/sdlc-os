"""Acceptance criterion: "Two tenants' runs, provisioned concurrently,
never share a node (verified by instance ID, not just by pool name)."
"""

from __future__ import annotations

import concurrent.futures

from tenant_cell.clock import FakeClock
from tenant_cell.provisioning_client import FakeProvisioningClient


def test_two_tenants_never_share_a_node_id_or_pool():
    clock = FakeClock()
    client = FakeProvisioningClient(clock)

    node_a = client.request_node(tenant_id="acme", base_environment="pilot-aws-g7e")
    node_b = client.request_node(tenant_id="globex", base_environment="pilot-aws-g7e")

    assert node_a.node_id != node_b.node_id
    assert node_a.pool_name != node_b.pool_name
    assert node_a.environment != node_b.environment


def test_no_shared_node_holds_under_concurrent_provisioning_requests():
    """Simulates many tenants requesting capacity concurrently (real
    threads, since FakeProvisioningClient's node_id allocation must be
    safe under real concurrent calls, not just sequential ones) and
    proves the client's own bookkeeping never assigns one node_id to more
    than one tenant.
    """
    clock = FakeClock()
    client = FakeProvisioningClient(clock)
    tenant_ids = [f"tenant-{i}" for i in range(50)]

    def provision(tid: str):
        return client.request_node(tenant_id=tid, base_environment="pilot-aws-g7e")

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        handles = list(pool.map(provision, tenant_ids))

    node_ids = [h.node_id for h in handles]
    pool_names = [h.pool_name for h in handles]

    # Every node_id is unique across all concurrently-provisioned tenants.
    assert len(set(node_ids)) == len(node_ids)
    # Every pool_name is unique -- distinct node pools, never a pool
    # shared between two tenants.
    assert len(set(pool_names)) == len(pool_names)

    # The client's own allocation ledger agrees: no node_id maps to more
    # than one tenant_id, verified by instance ID against the ledger, not
    # just by comparing the handles a single call happened to return.
    assert len(client.allocated_nodes) == len(tenant_ids)
    for tid in tenant_ids:
        owned = [nid for nid, owner in client.allocated_nodes.items() if owner == tid]
        assert len(owned) == 1


def test_replacement_node_for_same_tenant_still_never_collides_with_another_tenant():
    """A tenant's node gets reclaimed and replaced (spot interruption,
    Section 14.8) -- the replacement is a new node_id in the *same*
    tenant-scoped pool, and still structurally never collides with a
    different tenant's node_id.
    """
    clock = FakeClock()
    client = FakeProvisioningClient(clock)

    acme_node_1 = client.request_node(tenant_id="acme", base_environment="pilot-aws-g7e")
    globex_node = client.request_node(tenant_id="globex", base_environment="pilot-aws-g7e")
    acme_node_2 = client.request_node(tenant_id="acme", base_environment="pilot-aws-g7e")  # replacement

    assert acme_node_1.pool_name == acme_node_2.pool_name  # same tenant, same pool
    assert acme_node_1.node_id != acme_node_2.node_id  # different physical node
    assert acme_node_2.node_id != globex_node.node_id
    assert acme_node_2.pool_name != globex_node.pool_name
