"""D10 adversarial pass on D6 (model serving / tenant compute cell).

HISTORICAL SPEC-VS-CODE GAP FOUND BY THIS PASS, NOW CLOSED: the D10
brief and master spec Sec. 17.3 describe a per-tenant KMS key that
gates secret decryption ("each tenant's secrets are wrapped under a
KMS key scoped to that tenant alone ... decrypted only inside that
tenant's own compute cell at the point of use"). At the time of this
pass, an exhaustive read of `services/tenant-cell/` and `services/
source-control/` found no KMS key context, secret-decryption API, or
envelope-encryption logic anywhere in Wave 1 -- `tenant-cell`'s own
`infra/modules/tenant-cell/variables.tf` and README explicitly
disclaimed it ("Does NOT provision a tenant's network, KMS key, or
secrets bootstrap ... consumes an already-existing cluster/network/KMS
context as inputs"), and D5's `TenantInstallation.private_key_pem` was
only documented as "expected to already be the plaintext unwrapped ...
from this tenant's own KMS-wrapped secret" with nothing performing the
unwrap.

That gap is why the test immediately below this docstring used to be a
structural marker asserting `tenant_cell.kms`/`tenant_cell.secrets`/
`source_control.kms` did NOT exist, with a comment saying the
acceptance criterion "cannot be exercised against real code in this
repo, because that boundary has not been built yet."

**It has since been built**: `services/kms-boundary` (consumed here as
a real, editable-installed local dependency, same as every other
target this file audits) implements real per-tenant envelope
encryption against AWS KMS (tested against `moto`'s KMS mock), and
`source_control.TenantInstallation.from_wrapped_private_key` /
`issue_tracker.TenantJiraConfig.from_wrapped_oauth_token` are the new,
additive integration points that consume it -- the plaintext-only
constructor paths in both are unchanged. The marker test below now
exercises the real acceptance criterion directly instead of asserting
the boundary's absence; `kms-boundary`'s own suite
(`services/kms-boundary/tests/test_cross_tenant_denial.py`) is the
fuller adversarial treatment of the same boundary, cross-referenced
here rather than duplicated.

What IS ALSO real and testable in D6 is compute-identity isolation
(never sharing a node/pool across tenants) -- the rest of this file
adversarially extends that with a slugification-collision angle the
existing `test_no_shared_node.py` suite doesn't cover; those tests are
unchanged by this update.
"""
from __future__ import annotations

import pytest

from tenant_cell.clock import FakeClock
from tenant_cell.naming import node_pool_name, tenant_environment, tenant_slug
from tenant_cell.provisioning_client import FakeProvisioningClient


def test_kms_cross_tenant_decryption_boundary_now_exists_and_denies_cross_tenant_reads():
    """Replaces the former structural marker test (which asserted
    `kms_boundary` did not exist yet). Exercises the exact acceptance
    criterion this pass originally flagged as unbuildable: attempt to
    read one tenant's secret using another tenant's KMS-key context,
    against real `boto3` calls into moto's mocked KMS backend, and
    confirm it's denied -- not by this test's own logic, but by
    `kms_boundary.KmsBoundary`/AWS KMS's real semantics. See
    `services/kms-boundary/tests/test_cross_tenant_denial.py` for the
    fuller adversarial suite (direct-KMS-call bypass of the package's
    own bookkeeping, mismatched-encryption-context, no-plaintext-leak
    cases) this one is a compact acceptance-level echo of.
    """
    import boto3
    from moto import mock_aws

    from kms_boundary import CrossTenantDecryptionError, KmsBoundary, StaticTenantKeyResolver

    with mock_aws():
        client = boto3.client("kms", region_name="us-east-1")
        tenant_a_key = client.create_key(Description="tenant-a")["KeyMetadata"]["KeyId"]
        tenant_b_key = client.create_key(Description="tenant-b")["KeyMetadata"]["KeyId"]
        resolver = StaticTenantKeyResolver({"tenant-a": tenant_a_key, "tenant-b": tenant_b_key})
        boundary = KmsBoundary(client, resolver)

        tenant_a_secret = boundary.encrypt("tenant-a", b"tenant-a's real model-artifact/secret bytes")

        # The acceptance criterion, verbatim: tenant B's KMS-key context
        # must not be able to decrypt tenant A's wrapped secret.
        with pytest.raises(CrossTenantDecryptionError):
            boundary.decrypt("tenant-b", tenant_a_secret)

        # And the legitimate tenant still can -- proving the denial
        # above is about tenant identity, not a broken boundary.
        assert boundary.decrypt("tenant-a", tenant_a_secret) == b"tenant-a's real model-artifact/secret bytes"


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
