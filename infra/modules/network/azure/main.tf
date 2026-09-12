# network/azure — Phase 1.5 stub.
#
# Real implementation (not yet built — see README.md in this directory)
# would declare an azurerm_virtual_network + azurerm_subnet resources per
# tier (public/private/gpu, matching network/aws's three-tier layout), a
# NAT Gateway for private egress, and azurerm_network_security_group /
# azurerm_network_security_rule resources implementing the same
# egress-allowlist semantics as network/aws's security group. This file
# only satisfies the module contract so a composition under environments/
# can reference the azure variant without erroring; it provisions nothing.

terraform {
  required_version = ">= 1.6.0"
  # required_providers intentionally omitted — no provider is invoked here.
  # Real implementation would add:
  #   azurerm = { source = "hashicorp/azurerm", version = ">= 3.90" }
}

locals {
  not_implemented = "network/azure is a Phase 1.5 stub — see README.md"
}
