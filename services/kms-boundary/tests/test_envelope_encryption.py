"""Real envelope-encryption round trip against moto's mocked KMS."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from kms_boundary.envelope import KmsBoundary
from kms_boundary.key_resolver import StaticTenantKeyResolver
from kms_boundary.wrapped_secret import WrappedSecret


def test_round_trip_recovers_the_exact_plaintext(boundary):
    plaintext = b"-----BEGIN RSA PRIVATE KEY-----\nfake-but-secret-bytes\n-----END-----"
    wrapped = boundary.encrypt("tenant-a", plaintext)
    assert isinstance(wrapped, WrappedSecret)
    recovered = boundary.decrypt("tenant-a", wrapped)
    assert recovered == plaintext


def test_wrapped_secret_never_contains_the_plaintext_bytes(boundary):
    plaintext = b"super-secret-oauth-token-do-not-leak-me-anywhere"
    wrapped = boundary.encrypt("tenant-a", plaintext)
    assert plaintext not in wrapped.ciphertext
    assert plaintext not in wrapped.wrapped_data_key
    assert plaintext not in repr(wrapped).encode()


def test_payload_larger_than_kms_encrypt_4kb_limit_still_round_trips(boundary):
    """KMS's own `Encrypt` API rejects a plaintext over 4096 bytes
    (confirmed directly against moto below) -- genuine envelope
    encryption must not hit that ceiling since it only ever sends the
    32-byte data key to KMS, never the real payload."""
    big_plaintext = b"z" * 9000  # comfortably over the 4KB Encrypt ceiling
    wrapped = boundary.encrypt("tenant-a", big_plaintext)
    assert boundary.decrypt("tenant-a", wrapped) == big_plaintext


def test_kms_encrypt_api_itself_rejects_over_4kb_proving_envelope_encryption_is_load_bearing():
    """Negative control: calling KMS's `Encrypt` operation directly
    (not through this package) with a >4KB plaintext really is rejected
    by (moto's faithful reproduction of) KMS -- this is *why*
    `KmsBoundary` uses `GenerateDataKey` + local AES-GCM instead."""
    with mock_aws():
        client = boto3.client("kms", region_name="us-east-1")
        key_id = client.create_key()["KeyMetadata"]["KeyId"]
        with pytest.raises(Exception):
            client.encrypt(KeyId=key_id, Plaintext=b"z" * 9000)


def test_two_secrets_for_the_same_tenant_get_independent_data_keys_and_nonces(boundary):
    a = boundary.encrypt("tenant-a", b"secret-one")
    b = boundary.encrypt("tenant-a", b"secret-two")
    assert a.wrapped_data_key != b.wrapped_data_key
    assert a.nonce != b.nonce


def test_tampered_ciphertext_fails_local_aes_gcm_authentication(boundary):
    """Even with the *correct* tenant/key, a bit-flipped ciphertext
    must not decrypt -- proves the local AES-GCM step is a real,
    authenticated cipher, not just XOR-with-a-key."""
    wrapped = boundary.encrypt("tenant-a", b"do-not-tamper-with-me")
    tampered_bytes = bytearray(wrapped.ciphertext)
    tampered_bytes[0] ^= 0xFF
    tampered = type(wrapped)(
        tenant_id=wrapped.tenant_id,
        key_id=wrapped.key_id,
        wrapped_data_key=wrapped.wrapped_data_key,
        nonce=wrapped.nonce,
        ciphertext=bytes(tampered_bytes),
        encryption_context=wrapped.encryption_context,
    )
    with pytest.raises(Exception):
        boundary.decrypt("tenant-a", tampered)


def test_unknown_tenant_is_rejected_before_any_kms_call(boundary):
    from kms_boundary.errors import UnknownTenantError

    with pytest.raises(UnknownTenantError):
        boundary.encrypt("tenant-does-not-exist", b"whatever")
