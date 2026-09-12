"""kms-boundary: the per-tenant KMS envelope-encryption boundary that
CLAUDE.md's "Known cross-deliverable gaps" section and
`services/security-hardening/tests/test_d6_kms_and_node_isolation.py`
flagged as missing anywhere in this repo (spec Sec. 17.3: "each
tenant's secrets are wrapped under a KMS key scoped to that tenant
alone ... decrypted only inside that tenant's own compute cell at the
point of use").

Public surface:

- `WrappedSecret` -- the nominal type. The only way to obtain one is
  `KmsBoundary.encrypt()` returning it; there is no other constructor
  exported from this package (see `wrapped_secret.py`'s docstring).
- `KmsBoundary` -- real `boto3` envelope-encryption client:
  `.encrypt(tenant_id, plaintext) -> WrappedSecret` and
  `.decrypt(tenant_id, wrapped) -> bytes`.
- `TenantKeyResolver` / `StaticTenantKeyResolver` -- maps a tenant_id to
  *that tenant's own* KMS key id/alias. This package does not provision
  KMS keys itself (matching `tenant-cell`'s own disclaimer: "consumes an
  already-existing cluster/network/KMS context as inputs") -- the
  resolver is supplied by the caller.
- `KmsBoundaryError`, `UnknownTenantError`, `CrossTenantDecryptionError`
  -- see `errors.py`.

See README.md for the envelope-encryption design and what is real vs.
mocked, and SETUP.md for exactly what a human with a real AWS account
still has to do (this package never provisions keys, IAM policy, or
rotation -- it only calls Encrypt/GenerateDataKey/Decrypt against
whatever key context it is given).
"""

from __future__ import annotations

from kms_boundary.envelope import KmsBoundary
from kms_boundary.errors import CrossTenantDecryptionError, KmsBoundaryError, UnknownTenantError
from kms_boundary.key_resolver import StaticTenantKeyResolver, TenantKeyResolver
from kms_boundary.wrapped_secret import WrappedSecret

__all__ = [
    "KmsBoundary",
    "WrappedSecret",
    "TenantKeyResolver",
    "StaticTenantKeyResolver",
    "KmsBoundaryError",
    "UnknownTenantError",
    "CrossTenantDecryptionError",
]
