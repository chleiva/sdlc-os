"""Tenant-scoped naming, mirrored from the OpenTofu composition.

`infra/modules/tenant-cell/main.tf` (this deliverable's IaC layer) derives
every per-tenant resource name from the same two inputs this module uses:
a base environment name and a tenant id. That HCL computes:

    locals {
      tenant_environment = "${var.base_environment}-tenant-${local.tenant_slug}"
    }

and then calls F1's `gpu-node-pool` module with
`environment = local.tenant_environment`, whose own `local.name` (see
`infra/modules/gpu-node-pool/aws/main.tf`) is `"${var.environment}-gpu-node-pool"`.

This module is the Python-side restatement of that exact formula, used by
the control-plane logic (the fake provisioning client, the no-shared-node
test, cold-start tracking) so both layers agree on what a tenant's node
pool is called without the Python side needing to shell out to `tofu` to
find out. Keep the two formulas in lockstep by hand if either changes --
`tests/test_naming.py` pins the literal string shape so a drift is caught
as a test failure, not silently.

Tenant ids are slugified defensively (lowercased, non `[a-z0-9-]`
characters collapsed to `-`) *and* disambiguated with a short hash suffix
of the raw tenant id, so two distinct raw tenant ids that would otherwise
slugify to the same string (e.g. "Acme Corp!" and "acme_corp") still
produce two structurally distinct pool names -- required by the "no two
tenants ever resolve to the same pool identifier" acceptance criterion.
"""

from __future__ import annotations

import hashlib
import re

_SLUG_DISALLOWED = re.compile(r"[^a-z0-9]+")
_HASH_SUFFIX_LEN = 8


def tenant_slug(tenant_id: str) -> str:
    """A short, DNS/label-safe, collision-resistant slug for a tenant id.

    Not just a sanitized copy of `tenant_id`: a hash suffix derived from
    the *raw* (pre-sanitization) tenant id is always appended, so two
    different raw tenant ids can never collapse onto the same slug purely
    because sanitization discarded the characters that distinguished them.
    """
    if not tenant_id:
        raise ValueError("tenant_id must be a non-empty string")
    lowered = tenant_id.strip().lower()
    sanitized = _SLUG_DISALLOWED.sub("-", lowered).strip("-")
    if not sanitized:
        sanitized = "tenant"
    digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:_HASH_SUFFIX_LEN]
    return f"{sanitized}-{digest}"


def tenant_environment(base_environment: str, tenant_id: str) -> str:
    """The per-tenant environment name, matching the HCL `local.tenant_environment`."""
    if not base_environment:
        raise ValueError("base_environment must be a non-empty string")
    return f"{base_environment}-tenant-{tenant_slug(tenant_id)}"


def node_pool_name(base_environment: str, tenant_id: str) -> str:
    """The Karpenter NodePool name F1's `gpu-node-pool` module will create.

    Matches `infra/modules/gpu-node-pool/aws/main.tf`'s
    `local.name = "${var.environment}-gpu-node-pool"` exactly, with
    `var.environment` set (by the tenant-cell OpenTofu composition) to
    `tenant_environment(base_environment, tenant_id)`.
    """
    return f"{tenant_environment(base_environment, tenant_id)}-gpu-node-pool"


def model_serving_namespace(base_environment: str, tenant_id: str) -> str:
    """The Kubernetes namespace the tenant-cell composition deploys model-serving into."""
    return f"model-serving-{tenant_slug(tenant_id)}"
