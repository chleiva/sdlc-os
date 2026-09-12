# secrets/azure-key-vault — Phase 1.5 stub

Not a working Azure Key Vault implementation. Satisfies the module
contract (identical `variables.tf` / `outputs.tf` to
`secrets/aws-secrets-manager`) only, and preserves the same hard rule:
no secret value is ever an argument to a resource in this module, in the
real implementation or this stub. See `main.tf`.
