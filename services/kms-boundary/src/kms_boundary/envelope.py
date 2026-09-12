"""`KmsBoundary`: real per-tenant envelope encryption against AWS KMS.

Envelope encryption, not direct `Encrypt`: KMS's own `Encrypt` API caps
plaintext at 4KB, and real secrets this platform holds (an RSA App
private key, an OAuth bearer/refresh token) can exceed that. So
`encrypt()` asks KMS for a fresh data key via `GenerateDataKey`
(scoped to the tenant's own key_id), uses the *plaintext* copy of that
data key to encrypt the real payload locally with AES-256-GCM, keeps
only the *wrapped* (KMS-encrypted) copy of the data key, and discards
the plaintext data key immediately. `decrypt()` reverses this: ask KMS
to unwrap the data key (which only succeeds if the caller's tenant_id
resolves to the same key_id the secret was wrapped under), then decrypt
the payload locally.

Tenant isolation is enforced by AWS KMS itself, not by a comparison in
this module: a `CiphertextBlob` returned by `GenerateDataKey` is
cryptographically bound to the specific KMS key that produced it (real
KMS embeds a key identifier in the blob; moto's KMS mock faithfully
reproduces this -- see tests/test_cross_tenant_denial.py). Calling
`Decrypt` with a *different* key_id than the one that wrapped a given
blob is rejected by KMS with `AccessDeniedException` before this
module's own AES-GCM step ever runs. The `EncryptionContext` (always
carries this secret's `tenant_id`) is a second, independent KMS-enforced
binding: KMS also refuses to decrypt if the context supplied at decrypt
time doesn't match the context supplied at wrap time.
"""

from __future__ import annotations

import os

from botocore.exceptions import ClientError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from kms_boundary.errors import CrossTenantDecryptionError
from kms_boundary.key_resolver import TenantKeyResolver
from kms_boundary.wrapped_secret import WrappedSecret

_DATA_KEY_SPEC = "AES_256"
_NONCE_BYTES = 12  # 96-bit nonce, the AES-GCM-recommended size


class KmsBoundary:
    """Envelope-encryption client scoped by tenant.

    `kms_client` is a real `boto3` KMS client (or, in tests, one
    pointed at moto's mocked KMS backend via `@moto.mock_aws` --
    `boto3.client("kms", ...)` called inside that decorator/context
    manager transparently talks to the mock; no code in this module is
    aware of the difference). `key_resolver` maps a tenant_id to that
    tenant's own already-existing KMS key id/alias (see
    `key_resolver.py` -- this package does not provision keys).
    """

    def __init__(self, kms_client, key_resolver: TenantKeyResolver) -> None:
        self._kms = kms_client
        self._resolve_key = key_resolver

    def encrypt(self, tenant_id: str, plaintext: bytes) -> WrappedSecret:
        """Envelope-encrypt `plaintext` under `tenant_id`'s own KMS
        key. Raises `UnknownTenantError` (from the resolver) if
        `tenant_id` has no configured key -- before any KMS call is
        made."""
        key_id = self._resolve_key(tenant_id)
        context = {"tenant_id": tenant_id}

        response = self._kms.generate_data_key(
            KeyId=key_id,
            KeySpec=_DATA_KEY_SPEC,
            EncryptionContext=context,
        )
        wrapped_data_key: bytes = response["CiphertextBlob"]
        data_key = bytearray(response["Plaintext"])
        try:
            nonce = os.urandom(_NONCE_BYTES)
            ciphertext = AESGCM(bytes(data_key)).encrypt(nonce, plaintext, None)
        finally:
            # Best-effort zeroization of this process's one copy of the
            # plaintext data key. Python cannot guarantee no other copy
            # exists (immutable `bytes` objects from boto3/botocore may
            # already have been copied internally, and the GC offers no
            # secure-erase guarantee) -- this reduces the window, it
            # does not eliminate it. Documented as a known limitation
            # rather than a claimed guarantee; see README.md.
            for i in range(len(data_key)):
                data_key[i] = 0

        return WrappedSecret(
            tenant_id=tenant_id,
            key_id=key_id,
            wrapped_data_key=wrapped_data_key,
            nonce=nonce,
            ciphertext=ciphertext,
            encryption_context=tuple(sorted(context.items())),
        )

    def decrypt(self, tenant_id: str, wrapped: WrappedSecret) -> bytes:
        """Decrypt `wrapped` using `tenant_id`'s own KMS key context.

        Raises `UnknownTenantError` if `tenant_id` has no configured
        key. Raises `CrossTenantDecryptionError` if KMS refuses to
        unwrap `wrapped.wrapped_data_key` under that tenant's key --
        this is the real cross-tenant-denial boundary; see this
        module's docstring and tests/test_cross_tenant_denial.py.
        """
        key_id = self._resolve_key(tenant_id)

        try:
            response = self._kms.decrypt(
                CiphertextBlob=wrapped.wrapped_data_key,
                KeyId=key_id,
                EncryptionContext=wrapped.encryption_context_dict(),
            )
        except ClientError as exc:
            raise CrossTenantDecryptionError(
                f"tenant '{tenant_id}' (KMS key '{key_id}') cannot decrypt a secret "
                f"wrapped for tenant '{wrapped.tenant_id}' (KMS key '{wrapped.key_id}')"
            ) from exc

        data_key = bytearray(response["Plaintext"])
        try:
            return AESGCM(bytes(data_key)).decrypt(wrapped.nonce, wrapped.ciphertext, None)
        finally:
            for i in range(len(data_key)):
                data_key[i] = 0
