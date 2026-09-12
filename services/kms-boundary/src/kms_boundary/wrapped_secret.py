"""The `WrappedSecret` nominal type.

Same idiom this repo already uses for "evidence that a real check was
performed" (see `issue_tracker.gating.OptedInStory`,
`job_dispatcher.webhook_auth.AuthenticatedTrigger`,
`gates.self_approval.GateClearance`): a plain frozen dataclass with no
validation logic of its own, whose fields hold already-produced
evidence, paired with the discipline that exactly one function in this
package constructs it. Here that function is `KmsBoundary.encrypt()`
(see `envelope.py`) -- it is the only place in this package that builds
a `WrappedSecret`, and this module does not export any other
constructor.

Unlike `OptedInStory`/`AuthenticatedTrigger` (whose guarantee is a
boolean check that already happened), the guarantee behind a
`WrappedSecret` is cryptographic: a hand-fabricated instance with
garbage bytes is easy to construct (this is still a plain Python
dataclass -- nothing stops `WrappedSecret(...)` being called directly),
but it is cryptographically useless -- `KmsBoundary.decrypt()` will
reject it (KMS rejects a `wrapped_data_key` it never issued, and/or the
local AES-GCM authentication tag fails on tampered `ciphertext`). So the
real invariant this type protects is not "this object exists only if
constructed correctly" but "a plaintext value can only ever reach a
`WrappedSecret`'s `ciphertext` field by having already been through
`encrypt()`'s real KMS + AES-GCM path" -- there is no code path in this
package that would let a bare plaintext string flow into storage
without it. Downstream callers (`source-control`, `issue-tracker`) are
wired to accept a `WrappedSecret`, never a raw plaintext-or-wrapped
union, at their new alternate-constructor call sites -- see each
service's own integration module.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WrappedSecret:
    """An envelope-encrypted secret, scoped to exactly one tenant.

    - `wrapped_data_key` is the KMS `CiphertextBlob` from
      `GenerateDataKey` -- the per-secret AES-256 data key, wrapped
      under `key_id`. This package never persists the *unwrapped* data
      key anywhere; it exists only transiently inside `encrypt()`/
      `decrypt()`.
    - `ciphertext` + `nonce` are the actual secret payload, encrypted
      locally with that data key via AES-256-GCM -- never sent to KMS
      directly (KMS's own `Encrypt` API caps plaintext at 4KB; secrets
      like an RSA private key or an OAuth token comfortably exceed
      that in the wild, so genuine envelope encryption -- KMS wraps a
      small data key, the data key encrypts the real payload locally
      -- is used unconditionally, not just as a fallback for large
      inputs).
    - `encryption_context` is the KMS encryption context bound to
      `wrapped_data_key` at wrap time (always includes this secret's
      `tenant_id`) -- KMS itself refuses to decrypt if the context
      supplied at decrypt time doesn't match, an extra layer on top of
      the ciphertext-to-key binding that already makes cross-tenant
      decryption fail (see `envelope.py`).
    """

    tenant_id: str
    key_id: str
    wrapped_data_key: bytes
    nonce: bytes
    ciphertext: bytes
    encryption_context: tuple[tuple[str, str], ...]

    def encryption_context_dict(self) -> dict[str, str]:
        return dict(self.encryption_context)
