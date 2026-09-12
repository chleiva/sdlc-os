# SETUP — manual runbook for a human with real AWS account access

Everything in `src/` is real `boto3`/`botocore` code, tested against a
local `moto` mock (see README.md). **This package has never contacted
a real AWS KMS endpoint.** Nothing below can be done by an agent in
this environment — it all requires a real AWS account with IAM
admin/KMS admin rights. This document is the precise list of what a
human must do before pointing `KmsBoundary` at real KMS.

## 0. What's real vs. mocked, stated plainly

| Piece | Real | Mocked |
|---|---|---|
| `GenerateDataKey`/`Decrypt` request/response handling | Yes — `envelope.py` | The KMS service on the other end (`moto`'s KMS backend in tests) |
| AES-256-GCM local envelope encryption | Yes — `envelope.py`, `cryptography` library | n/a |
| Cross-tenant denial semantics (key-to-ciphertext binding, encryption context matching) | Yes, this package relies on and tests against them | Real AWS KMS's actual implementation of these semantics is assumed identical to moto's reproduction — see step 5 |
| Per-tenant KMS key/alias creation | n/a | **Cannot be done in this environment at all** |
| IAM policy scoping which principal may call which KMS action for which key | n/a | **Cannot be done in this environment at all** |
| Key rotation policy | n/a | **Cannot be done in this environment at all** |

## 1. Create one KMS key per tenant (or a per-tenant alias under a shared key)

Per spec §17.3 ("each tenant's secrets are wrapped under a KMS key
scoped to that tenant alone"), pick one of two real, equally valid AWS
patterns:

- **One CMK per tenant** (simplest to reason about; most direct
  isolation — a compromised IAM policy for tenant A's key structurally
  cannot touch tenant B's key at all):
  ```
  aws kms create-key --description "sdlc-auto tenant <tenant_id> secrets key" \
    --tags TagKey=tenant_id,TagValue=<tenant_id>
  aws kms create-alias --alias-name alias/sdlc-auto-tenant-<tenant_id> \
    --target-key-id <key-id-from-above>
  ```
- **One shared CMK, one alias per tenant, key policy scoped per
  alias's grants**: cheaper (AWS caps CMKs per account/region; a large
  tenant count may need this), but requires the IAM/key-policy scoping
  in step 2 to do the real isolation work instead of key separation
  alone — prefer the one-CMK-per-tenant pattern unless tenant count
  makes it impractical.

Either way, record the resulting `key_id` or `alias/...` string per
tenant — that is exactly what this package's `TenantKeyResolver` (e.g.
`StaticTenantKeyResolver`) needs, fed from wherever your real tenant
onboarding record lives (this package does not read it from anywhere
on its own).

## 2. IAM policy: scope who can call Encrypt/Decrypt/GenerateDataKey for which key

For each tenant's key (or alias), attach a key policy / IAM policy pair
such that:

- Only the specific principal/role that this tenant's compute cell
  (D6/`tenant-cell`) runs as may call `kms:GenerateDataKey` and
  `kms:Decrypt` for that tenant's key.
- No principal is granted those actions for *more than one* tenant's
  key, unless that principal is a genuinely cross-tenant platform
  operator role — and even then, prefer scoping such a role to
  `kms:Decrypt` only where an audited, logged use case requires it
  (e.g. break-glass), never as this platform's normal operating mode.
- Grant `kms:Encrypt`/`kms:GenerateDataKey` narrowly to whatever
  process performs onboarding/secret-wrapping for that tenant (likely
  the same principal as above, or a dedicated onboarding role) — do
  **not** grant it broadly "just in case."
- Enable **CloudTrail** logging for KMS API calls (on by default for
  management events in most accounts) and confirm KMS data-plane
  events (`Decrypt`, `GenerateDataKey`) are captured — Sec. 17.1's NHI
  audit trail depends on being able to reconcile who decrypted what,
  when.

## 3. Key rotation policy

- Enable **automatic annual rotation** for each CMK
  (`aws kms enable-key-rotation --key-id <key-id>`) as the baseline —
  AWS rotates the key material transparently; existing `WrappedSecret`
  values remain decryptable (KMS tracks old key material internally).
- For a tenant offboarding or a suspected-compromised key, use
  `aws kms schedule-key-deletion` (7–30 day waiting period, cannot be
  reversed after it completes) — confirm no `WrappedSecret` still
  needs that key before scheduling deletion; there is no code in this
  package that re-wraps existing secrets under a replacement key, that
  re-wrap step belongs to whatever real secrets-management workflow
  owns the tenant's onboarding record.

## 4. Wire a real `TenantKeyResolver`

This is deliberately not automated by this package — a real
deployment's tenant onboarding/provisioning flow owns populating this,
reading each tenant's `key_id`/alias from wherever that record lives at
process start:

```python
import boto3
from kms_boundary import KmsBoundary, StaticTenantKeyResolver

resolver = StaticTenantKeyResolver({
    "tenant-acme": "alias/sdlc-auto-tenant-acme",
    "tenant-globex": "arn:aws:kms:us-east-1:111122223333:key/<uuid>",
})
kms_client = boto3.client("kms", region_name="<your region>")
boundary = KmsBoundary(kms_client, resolver)

wrapped = boundary.encrypt("tenant-acme", plaintext_private_key_pem)
# ... persist `wrapped` (it is safe to store; it contains no plaintext) ...
plaintext_private_key_pem = boundary.decrypt("tenant-acme", wrapped)
```

## 5. Smoke-test against a real (non-production) KMS key before going live

Once step 1–4 are done, before wiring this into a production tenant
onboarding flow:

1. Create one disposable, throwaway KMS key in a sandbox/dev AWS
   account (step 1).
2. Run `encrypt()`/`decrypt()` once by hand (e.g. in a Python REPL)
   against it, and confirm the round trip works exactly like the
   `moto`-backed test suite says it should.
3. Confirm the cross-tenant denial this package's tests prove against
   `moto` holds against the *real* service too: create a second
   throwaway key, and confirm a `WrappedSecret` produced under the
   first key really is rejected (`AccessDeniedException`) when you
   attempt to decrypt it using the second key's `key_id` — this
   validates the assumption flagged in the README's "What's real vs.
   mocked" table, that moto's KMS semantics match real AWS KMS's.
4. Only then point a real tenant's onboarding flow at this package.

## 6. Ongoing operational notes

- **NHI inventory (spec §17.1)**: record each tenant's key_id/alias
  alongside its other registered identities (GitHub App installation,
  Jira OAuth app — see `source-control`/`issue-tracker`'s own SETUP.md)
  in whatever system holds the organization's non-human-identity
  inventory.
- **Never** let two tenants' `TenantKeyResolver` entries resolve to the
  same underlying CMK unless you have deliberately chosen the
  shared-CMK-plus-scoped-alias-grants pattern from step 1 and verified
  the IAM policy actually enforces per-alias isolation — a resolver
  that accidentally maps two tenants to the same key defeats this
  package's entire isolation guarantee, since KMS itself would then
  correctly allow the cross-tenant decrypt (there would be nothing
  cross-tenant about it from KMS's point of view).
