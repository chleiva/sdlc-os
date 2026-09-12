# kms-boundary — the per-tenant KMS secret-decryption boundary

This package exists to close a specific, previously-documented gap.
`CLAUDE.md`'s "Known cross-deliverable gaps" section said:

> No KMS/secret-decryption boundary exists in code anywhere (spec
> §17.3) — D10's audit went looking for it in D5/D6 and confirmed the
> gap is real, not just unaudited.

And `services/security-hardening/tests/test_d6_kms_and_node_isolation.py`
had a structural marker test asserting `tenant_cell.kms`,
`tenant_cell.secrets`, and `source_control.kms` do **not** exist, with
a docstring saying the real acceptance criterion ("attempt to read one
tenant's secret using another tenant's KMS-key context and confirm
it's denied") "cannot be exercised against real code in this repo,
because that boundary has not been built yet anywhere in Wave 1."

`kms-boundary` is that boundary, built for real. The marker test above
has been updated to import this package instead of asserting its
absence (see "What changed in security-hardening" below).

## What this package does

Real per-tenant **envelope encryption** against AWS KMS:

- `KmsBoundary.encrypt(tenant_id, plaintext) -> WrappedSecret` — asks
  the tenant's own KMS key (via `GenerateDataKey`) for a fresh
  AES-256 data key, encrypts `plaintext` locally with that data key
  using AES-256-GCM, discards the plaintext data key, and returns a
  `WrappedSecret` carrying only the KMS-wrapped data key and the local
  ciphertext.
- `KmsBoundary.decrypt(tenant_id, wrapped) -> bytes` — asks KMS to
  unwrap the data key under `tenant_id`'s own key, then decrypts the
  payload locally.

**Why envelope encryption and not KMS's `Encrypt` API directly**: real
KMS caps `Encrypt`'s plaintext at 4KB. Real secrets this platform holds
— an RSA GitHub App private key, a Jira OAuth token — can exceed that.
Envelope encryption (KMS wraps a small data key; the data key encrypts
the real payload locally) is the standard AWS-recommended pattern for
exactly this reason, and this package uses it unconditionally, not
just as a large-payload fallback. `tests/test_envelope_encryption.py`
includes a negative control proving KMS's own `Encrypt` really does
reject a >4KB plaintext, to make that reasoning testable rather than
asserted.

## Real tenant isolation, not just per-tenant labeling

Each tenant maps to its own, already-existing KMS key (a real, distinct
`key_id`/alias per tenant — see "What this package does NOT do"
below). Cross-tenant decryption is denied by **AWS KMS itself**, via
two independent, KMS-enforced bindings — not by a string comparison
in this package:

1. **Key-to-ciphertext binding.** The `CiphertextBlob` a `GenerateDataKey`
   call returns is cryptographically bound to the specific KMS key
   that produced it. Calling `Decrypt` with a *different* `KeyId` than
   the one that wrapped a given blob is rejected by KMS with
   `AccessDeniedException`, before this package's own AES-GCM step
   ever runs.
2. **Encryption context binding.** Every wrap carries the secret's
   `tenant_id` as its KMS `EncryptionContext`. KMS also refuses to
   decrypt if the context supplied at decrypt time doesn't match the
   context supplied at wrap time — independent defense in depth on top
   of (1).

`tests/test_cross_tenant_denial.py` is the real acceptance test: it
proves tenant A's `WrappedSecret` cannot be decrypted using tenant B's
KMS key context, including a variant
(`test_denial_is_enforced_by_kms_itself_not_by_a_string_comparison`)
that calls boto3's KMS `Decrypt` operation directly — bypassing this
package's own bookkeeping entirely — to prove the denial is a property
of KMS itself, not of code in this repository.

## The `WrappedSecret` nominal type

Same idiom already used elsewhere in this repo for "evidence a real
check was performed" (`issue_tracker.gating.OptedInStory`,
`job_dispatcher.webhook_auth.AuthenticatedTrigger`,
`gates.self_approval.GateClearance`): a plain frozen dataclass with no
validation logic of its own, constructed in exactly one place —
`KmsBoundary.encrypt()`. See `src/kms_boundary/wrapped_secret.py`'s
module docstring for how this type's guarantee differs from those
(cryptographic uselessness of a fabricated instance, rather than a
boolean check already having passed) and why that's still the right
shape: nothing in `source-control` or `issue-tracker`'s new integration
points accepts a raw plaintext value where a `WrappedSecret` is
expected, so a plaintext can't accidentally flow into a storage call
without having gone through `encrypt()`.

## What this package does NOT do (this is deliberate — see SETUP.md)

Matches `services/tenant-cell/infra/modules/tenant-cell/variables.tf`'s
own disclaimer pattern ("does NOT provision a tenant's network, KMS
key, or secrets bootstrap ... consumes an already-existing
cluster/network/KMS context as inputs"):

- Does **not** create, alias, or rotate any KMS key.
- Does **not** decide which principal/role may call
  `Encrypt`/`Decrypt`/`GenerateDataKey` for which key (IAM policy).
- Does **not** provision or own the tenant→key mapping — a
  `TenantKeyResolver` (see `key_resolver.py`) is supplied by the
  caller, and is expected to come from that tenant's own real
  onboarding/secrets record.

See `SETUP.md` for exactly what a human with real AWS access has to do.

## What's real vs. mocked in this deliverable

| Piece | Status |
|---|---|
| `boto3`/`botocore` request shapes for `GenerateDataKey`/`Decrypt` | Real |
| AES-256-GCM local envelope encryption (`cryptography` library) | Real |
| Cross-tenant KMS denial (key-to-ciphertext binding + encryption context) | Real, enforced by (moto's faithful reproduction of) actual KMS semantics |
| The AWS KMS service on the other end | Mocked — `moto`'s KMS backend (`@moto.mock_aws` / `mock_aws()`), exercised via real `boto3` calls, never a hand-rolled fake client |
| Per-tenant KMS key/alias provisioning, IAM policy, key rotation | **Not done here** — see SETUP.md |

### Why `moto` (new test-only dependency, scoped to this package)

No service in this repo depended on `boto3`/`moto` before this one (a
repo-wide grep confirmed this). `moto[kms]` is a reasonable new
test-only dependency for `kms-boundary` specifically: it lets this
package's tests make real `boto3` calls against a locally-mocked KMS
backend — real request/response shapes and real botocore validation
(e.g. the `Encrypt` API's 4KB plaintext ceiling, and the
ciphertext-to-key binding this package's isolation guarantee depends
on) — rather than a hand-rolled fake KMS client that could silently
drift from real AWS KMS semantics. It is declared only in this
package's own `[project.optional-dependencies].dev`, not added to any
other service.

## Install & run tests

```bash
cd services/kms-boundary
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -v
```

15 tests, all real `boto3` calls against moto's mocked KMS backend —
no external AWS account needed to run this suite.

## Consumers

`services/source-control` and `services/issue-tracker` both depend on
this package as a real, editable-installed local dependency (the same
pattern `job-dispatcher` uses for `run-registry`/`issue-tracker` — see
their own `pyproject.toml`/README for the exact `pip install -e
../kms-boundary` step), and each adds one new, additive, alternate
constructor that accepts a `WrappedSecret` + this package's `decrypt()`
instead of only a raw plaintext credential — see each service's own
README for the exact call site.
