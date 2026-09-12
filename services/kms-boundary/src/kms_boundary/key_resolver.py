"""Tenant -> KMS key resolution.

This package does not provision KMS keys, aliases, or IAM policy --
same disclaimer pattern as `services/tenant-cell/infra/modules/
tenant-cell/variables.tf` ("does NOT provision a tenant's ... KMS key
... consumes an already-existing cluster/network/KMS context as
inputs"). A `TenantKeyResolver` is how the caller (whoever owns real
per-tenant KMS provisioning -- the not-yet-written tenant
onboarding/secrets module `security-hardening`'s audit pointed to)
tells this package which *already-existing* key belongs to which
tenant. See SETUP.md for what a human with real AWS access has to do
to create that mapping for real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from kms_boundary.errors import UnknownTenantError

# A resolver is just "tenant_id -> that tenant's own KMS key id/alias."
# `StaticTenantKeyResolver` below is the obvious in-memory case; nothing
# stops a caller from supplying any other callable (e.g. one backed by
# a real secrets/config service) as long as it matches this shape.
TenantKeyResolver = Callable[[str], str]


@dataclass(frozen=True)
class StaticTenantKeyResolver:
    """The simple, common case: an in-memory tenant_id -> key_id/alias
    map, handed in by the caller (e.g. loaded from that tenant's own
    onboarding record). Real KMS key ids/aliases go in directly --
    e.g. `{"tenant-acme": "arn:aws:kms:us-east-1:111122223333:key/
    <uuid>", "tenant-globex": "alias/tenant-globex"}`. Distinct tenants
    MUST map to distinct keys/aliases for real isolation -- this class
    does not itself enforce that (it cannot know whether two aliases
    happen to resolve to the same underlying CMK); see SETUP.md for
    the real provisioning discipline that guarantees it operationally.
    """

    tenant_to_key_id: Mapping[str, str]

    def __call__(self, tenant_id: str) -> str:
        try:
            return self.tenant_to_key_id[tenant_id]
        except KeyError as exc:
            raise UnknownTenantError(f"no KMS key configured for tenant '{tenant_id}'") from exc
