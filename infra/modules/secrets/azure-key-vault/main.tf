# secrets/azure-key-vault — Phase 1.5 stub.
#
# Real implementation would declare an azurerm_key_vault plus
# azurerm_key_vault_secret *containers* only (this module never sets a
# secret's value argument, the same boundary as secrets/aws-secrets-
# manager) and azurerm_key_vault_access_policy read grants. This file only
# satisfies the module contract; it provisions nothing.

terraform {
  required_version = ">= 1.6.0"
  # Real implementation would add:
  #   azurerm = { source = "hashicorp/azurerm", version = ">= 3.90" }
}

locals {
  not_implemented = "secrets/azure-key-vault is a Phase 1.5 stub — see README.md"
}
