from __future__ import annotations

from tenant_cell.naming import model_serving_namespace, node_pool_name, tenant_environment, tenant_slug


def test_node_pool_name_matches_hcl_formula():
    # Mirrors infra/modules/gpu-node-pool/aws/main.tf's
    # local.name = "${var.environment}-gpu-node-pool", with var.environment
    # set by the tenant-cell composition to tenant_environment(...).
    pool = node_pool_name("pilot-aws-g7e", "acme")
    env = tenant_environment("pilot-aws-g7e", "acme")
    assert pool == f"{env}-gpu-node-pool"
    assert pool.startswith("pilot-aws-g7e-tenant-")


def test_distinct_tenants_never_produce_the_same_pool_name():
    names = {node_pool_name("pilot-aws-g7e", f"tenant-{i}") for i in range(200)}
    assert len(names) == 200


def test_slug_disambiguates_ids_that_sanitize_to_the_same_string():
    # "Acme Corp!" and "acme_corp" both sanitize to "acme-corp" before the
    # hash suffix is appended -- the hash suffix must keep them distinct.
    a = tenant_slug("Acme Corp!")
    b = tenant_slug("acme_corp")
    assert a != b
    assert node_pool_name("pilot-aws-g7e", "Acme Corp!") != node_pool_name(
        "pilot-aws-g7e", "acme_corp"
    )


def test_model_serving_namespace_is_tenant_scoped():
    ns_a = model_serving_namespace("pilot-aws-g7e", "acme")
    ns_b = model_serving_namespace("pilot-aws-g7e", "globex")
    assert ns_a != ns_b
    assert "acme" in ns_a
