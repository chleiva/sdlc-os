# secrets/vault — Phase 1.5 stub (self-hosted Vault, for bare metal or a
# smaller provider without a native secrets service — spec §14.6).
#
# Real implementation would use the `vault` Terraform/OpenTofu provider to
# declare mount points and read-only policies against an already-running
# Vault cluster (Vault's own bring-up is out of this module's scope — it
# is itself infrastructure this environment would stand up first), never
# a secret value written into a vault_kv_secret_v2 resource's data
# argument. This file only satisfies the module contract; it provisions
# nothing.

terraform {
  required_version = ">= 1.6.0"
  # Real implementation would add:
  #   vault = { source = "hashicorp/vault", version = ">= 4.0" }
}

locals {
  not_implemented = "secrets/vault is a Phase 1.5 stub — see README.md"
}
