"""THE acceptance-criterion test: CLAUDE.md's "Known cross-deliverable
gaps" section and `security-hardening`'s structural marker test both
say this exact scenario -- "attempt to read one tenant's secret using
another tenant's KMS-key context and confirm it's denied" -- "cannot be
exercised against real code in this repo, because that boundary has
not been built yet." This file exercises it for real, against real
`boto3` calls into moto's mocked KMS backend (not a hand-rolled fake).
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from kms_boundary.errors import CrossTenantDecryptionError, UnknownTenantError
from kms_boundary.key_resolver import StaticTenantKeyResolver


def test_tenant_bs_kms_key_cannot_decrypt_tenant_as_wrapped_secret(boundary):
    """The core acceptance criterion, verbatim: tenant A's wrapped
    secret, decrypted using tenant B's own KMS key context, is denied
    -- not silently allowed, not routed to the wrong tenant."""
    tenant_a_secret = boundary.encrypt("tenant-a", b"tenant-a's real private key material")

    with pytest.raises(CrossTenantDecryptionError):
        boundary.decrypt("tenant-b", tenant_a_secret)

    # And the legitimate owner still can, proving the denial above was
    # about tenant identity, not a broken encrypt/decrypt path.
    assert boundary.decrypt("tenant-a", tenant_a_secret) == b"tenant-a's real private key material"


def test_denial_is_enforced_by_kms_itself_not_by_a_string_comparison(moto_kms, two_tenant_keys):
    """Bypass this package's own tenant bookkeeping entirely and prove
    the denial happens inside the (mocked) KMS service: take the raw
    `wrapped_data_key` blob KMS issued for tenant A's key, and call
    boto3's KMS `Decrypt` operation directly with tenant B's key_id.
    Real KMS rejects this because the ciphertext blob is
    cryptographically bound to the specific key that produced it; this
    proves the boundary this package relies on is real, not an
    assumption."""
    from kms_boundary.envelope import KmsBoundary

    resolver = StaticTenantKeyResolver(two_tenant_keys)
    boundary = KmsBoundary(moto_kms, resolver)
    wrapped = boundary.encrypt("tenant-a", b"payload")

    with pytest.raises(ClientError) as excinfo:
        moto_kms.decrypt(
            CiphertextBlob=wrapped.wrapped_data_key,
            KeyId=two_tenant_keys["tenant-b"],
            EncryptionContext=wrapped.encryption_context_dict(),
        )
    assert excinfo.value.response["Error"]["Code"] == "AccessDeniedException"


def test_mismatched_encryption_context_is_also_denied_even_with_the_right_key(moto_kms, two_tenant_keys):
    """A second, independent KMS-enforced binding: even the *correct*
    key_id refuses to decrypt if the encryption context doesn't match
    what was supplied at wrap time -- defense in depth beyond the
    key-to-ciphertext binding alone."""
    key_id = two_tenant_keys["tenant-a"]
    dk = moto_kms.generate_data_key(
        KeyId=key_id, KeySpec="AES_256", EncryptionContext={"tenant_id": "tenant-a"}
    )
    with pytest.raises(ClientError):
        moto_kms.decrypt(
            CiphertextBlob=dk["CiphertextBlob"],
            KeyId=key_id,
            EncryptionContext={"tenant_id": "tenant-b"},
        )


def test_unregistered_tenant_cannot_even_attempt_decryption(boundary):
    """A tenant_id with no configured key at all is rejected by the
    resolver before ever reaching KMS -- fail-closed on the unknown
    case, same shape as `source_control.InstallationRegistry.resolve`."""
    tenant_a_secret = boundary.encrypt("tenant-a", b"payload")
    with pytest.raises(UnknownTenantError):
        boundary.decrypt("tenant-does-not-exist", tenant_a_secret)


def test_no_plaintext_leaks_through_a_denied_cross_tenant_attempt(boundary):
    """A failed cross-tenant decrypt must not partially recover or leak
    the plaintext via the exception -- the error carries only tenant
    and key identifiers, never key material or payload bytes."""
    secret = b"the-actual-secret-bytes-must-never-appear-below"
    wrapped = boundary.encrypt("tenant-a", secret)
    with pytest.raises(CrossTenantDecryptionError) as excinfo:
        boundary.decrypt("tenant-b", wrapped)
    assert secret not in str(excinfo.value).encode()
