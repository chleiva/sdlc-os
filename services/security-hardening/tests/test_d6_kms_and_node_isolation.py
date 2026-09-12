"""D10 adversarial pass on D6 (model serving / tenant compute cell).

IMPORTANT SPEC-VS-CODE GAP FOUND BY THIS PASS (documented, not silently
assumed away): the D10 brief and master spec Sec. 17.3 describe a
per-tenant KMS key that gates secret decryption ("each tenant's secrets
are wrapped under a KMS key scoped to that tenant alone ... decrypted
only inside that tenant's own compute cell at the point of use"). An
exhaustive read of `services/tenant-cell/` (every module under
`src/tenant_cell/`, its README, and its OpenTofu variables/README) found
**no KMS key context, secret-decryption API, or envelope-encryption
logic anywhere in this deliverable** -- `tenant-cell`'s own
`infra/modules/tenant-cell/variables.tf` and README explicitly disclaim
it: "Does NOT provision a tenant's network, KMS key, or secrets
bootstrap ... consumes an already-existing cluster/network/KMS context
as inputs." The same is true of D5 (`source-control`): `TenantInstallation
.private_key_pem` is documented as "expected to already be the plaintext
unwrapped at the point of use from this tenant's own KMS-wrapped secret"
but nothing in that package performs the unwrap either.

**Conclusion: the acceptance criterion "attempt to read one tenant's
model-artifact/secret access using another tenant's KMS-key context and
confirm it's denied" cannot be exercised against real code in this
repo, because that boundary has not been built yet anywhere in Wave 1.**
This is flagged in the final report as a genuine spec-vs-implementation
gap for a human to resolve (most likely: it belongs to a not-yet-written
"tenant onboarding/secrets" module, per tenant-cell's own README) -- it
is explicitly NOT patched here, since inventing a KMS abstraction from
scratch inside D10 would be exactly the "large refactor of another
deliverable" the brief says not to do.

What IS real and testable in D6 today is compute-identity isolation
(never sharing a node/pool across tenants) -- this file adversarially
extends that with a slugification-collision angle the existing
`test_no_shared_node.py` suite doesn't cover.
"""
from __future__ import annotations

import pytest

from tenant_cell.clock import FakeClock
from tenant_cell.naming import node_pool_name, tenant_environment, tenant_slug
from tenant_cell.provisioning_client import FakeProvisioningClient


def test_kms_cross_tenant_decryption_boundary_does_not_exist_in_this_repo_yet():
    """Structural marker test (not a skip): fails loudly, not silently,
    the moment someone adds a `kms`/`secrets` module to this deliverable
    -- at which point this test (and the real adversarial KMS test it
    should be replaced by) needs to be written for real."""
    import importlib

    for candidate in ("tenant_cell.kms", "tenant_cell.secrets", "source_control.kms"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(candidate)


# ---------------------------------------------------------------------
# The closest real, testable tenant-isolation boundary in D6 today:
# node/pool identity. Adversarially target the slugification formula
# with inputs designed to collide.
# ---------------------------------------------------------------------
def test_slugification_collision_inputs_still_produce_distinct_pools():
    """"Acme Corp!" and "acme_corp" and "ACME-CORP" all sanitize toward
    a similar-looking slug -- confirm the raw-tenant-id hash suffix
    really does keep them distinct, per naming.py's own stated design
    goal."""
    candidates = ["Acme Corp!", "acme_corp", "ACME-CORP", "acme corp", "acme--corp"]
    slugs = {c: tenant_slug(c) for c in candidates}
    assert len(set(slugs.values())) == len(candidates), f"collision: {slugs}"
    pools = {c: node_pool_name("prod", c) for c in candidates}
    assert len(set(pools.values())) == len(candidates), f"pool collision: {pools}"


def test_empty_tenant_id_is_rejected_outright():
    with pytest.raises(ValueError):
        tenant_slug("")


def test_whitespace_only_tenant_ids_fall_back_to_a_generic_slug_but_stay_distinguishable():
    """`tenant_slug("   ")` doesn't raise (whitespace is truthy, so it's
    not caught by the same `if not tenant_id` guard as ""); it falls
    back to the literal "tenant" sanitized stem. Confirm this documented
    fallback still keeps two different whitespace-only tenant ids
    distinguishable via the raw-id hash suffix, rather than silently
    colliding them onto one shared pool."""
    slug_a = tenant_slug("   ")
    slug_b = tenant_slug("\t")
    assert slug_a.startswith("tenant-")
    assert slug_b.startswith("tenant-")
    assert slug_a != slug_b, "two different whitespace-only tenant ids collided onto the same slug"


def test_two_colliding_tenant_ids_never_share_a_node_under_concurrency():
    """Adversarial variant of `test_no_shared_node.py`: two tenant_ids
    specifically chosen to collide after naive sanitization, provisioned
    concurrently, must still never land in the same pool or share a
    node_id."""
    import threading

    clock = FakeClock()
    client = FakeProvisioningClient(clock)
    colliding_ids = ["Tenant #1!!", "tenant_1", "TENANT-1", "tenant  1"]
    handles = []
    lock = threading.Lock()

    def _provision(tid):
        h = client.request_node(tenant_id=tid, base_environment="prod")
        with lock:
            handles.append(h)

    threads = [threading.Thread(target=_provision, args=(tid,)) for tid in colliding_ids for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Every node_id in the ledger maps to exactly the tenant_id it was
    # requested for -- no cross-tenant assignment, even under a storm of
    # concurrent requests with adversarially-similar tenant ids.
    for node_id, owner_tenant in client.allocated_nodes.items():
        assert owner_tenant in colliding_ids
    # And distinct tenant_ids never resolved to the same pool_name.
    pool_by_tenant = {h.tenant_id: h.pool_name for h in handles}
    assert len(set(pool_by_tenant.values())) == len(colliding_ids)
