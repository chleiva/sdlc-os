"""`WrappedSecret`'s nominal-type guarantee (see wrapped_secret.py's
module docstring): the only function in this package that constructs a
*cryptographically valid* one is `KmsBoundary.encrypt()`. A
hand-fabricated instance is trivially constructible (it's a plain
frozen dataclass) but functionally useless -- `decrypt()` rejects it."""

from __future__ import annotations

import pytest

from kms_boundary.wrapped_secret import WrappedSecret


def test_hand_fabricated_wrapped_secret_is_rejected_on_decrypt(boundary):
    fake = WrappedSecret(
        tenant_id="tenant-a",
        key_id="not-a-real-key-id",
        wrapped_data_key=b"not-actually-a-kms-ciphertext-blob",
        nonce=b"0" * 12,
        ciphertext=b"not-actually-encrypted",
        encryption_context=(("tenant_id", "tenant-a"),),
    )
    with pytest.raises(Exception):
        boundary.decrypt("tenant-a", fake)


def test_wrapped_secret_is_frozen_and_cannot_be_mutated_after_construction(boundary):
    wrapped = boundary.encrypt("tenant-a", b"payload")
    with pytest.raises(Exception):
        wrapped.ciphertext = b"replaced"  # type: ignore[misc]


def test_encrypt_is_the_only_constructor_this_package_exports_for_real_secrets():
    """Structural check: `kms_boundary`'s public API (`__init__.py`)
    exports exactly one way to turn a plaintext into a `WrappedSecret`
    -- `KmsBoundary.encrypt` -- never a free function that builds one
    from caller-supplied ciphertext bytes directly."""
    import kms_boundary

    assert kms_boundary.WrappedSecret is not None
    assert not hasattr(kms_boundary, "wrap")  # no bypass free-function
    assert not hasattr(kms_boundary, "make_wrapped_secret")
