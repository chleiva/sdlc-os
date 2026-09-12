# secrets/vault — Phase 1.5 stub

Not a working self-hosted Vault implementation. Satisfies the module
contract (identical `variables.tf` / `outputs.tf` to
`secrets/aws-secrets-manager`) only, and preserves the same hard rule:
no secret value is ever an argument to a resource in this module, in the
real implementation or this stub. Note this variant also implies Vault
itself must already be running somewhere (bare metal / smaller-provider
target) — this module configures access to it, it does not stand Vault
up. See `main.tf`.
