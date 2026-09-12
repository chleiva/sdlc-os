"""Errors raised by this package. All are fail-closed: every failure
mode here means "no plaintext was returned," never a silent fallback."""

from __future__ import annotations


class KmsBoundaryError(Exception):
    """Base class for every error this package raises."""


class UnknownTenantError(KmsBoundaryError):
    """Raised by a `TenantKeyResolver` when it has no KMS key configured
    for the given tenant_id. Raised *before* any KMS call is attempted
    -- an unrecognized tenant never gets far enough to touch KMS at
    all, mirroring `source_control.InstallationRegistry.resolve`'s
    fail-closed-on-unknown-tenant shape."""


class CrossTenantDecryptionError(KmsBoundaryError):
    """Raised when `KmsBoundary.decrypt()` is called with a tenant_id
    whose resolved KMS key cannot decrypt the given `WrappedSecret`.

    This is the real acceptance criterion CLAUDE.md's "Known
    cross-deliverable gaps" section and
    `security-hardening`'s structural marker test flagged as
    unbuildable until this package existed: it wraps a genuine AWS KMS
    `AccessDeniedException`/`InvalidCiphertextException` (or moto's
    faithful reproduction of them) -- the denial is enforced by KMS
    itself (the ciphertext blob is cryptographically bound to the key
    that produced it, and to the encryption context supplied at wrap
    time), not by a string comparison in this package.
    """
