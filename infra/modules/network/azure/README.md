# network/azure — Phase 1.5 stub

Not a working Azure network implementation — satisfies the module
contract (same `variables.tf` / `outputs.tf` as `network/aws`) only, so
`infra/environments/prod-azure` has a valid module path and the
module-contract rule (spec §14.3, §14.10) is reviewable ahead of the real
build. `main.tf` declares no provider and creates no resources.

## What the real implementation needs (Phase 1.5 portability proof, §14.10, §21)

- `azurerm_virtual_network` + `azurerm_subnet` per tier (public/private/
  gpu), matching `network/aws`'s three-tier layout.
- `azurerm_nat_gateway` for private-subnet egress.
- `azurerm_network_security_group` / `azurerm_network_security_rule`
  implementing the same default-deny + explicit-allowlist egress
  semantics as `network/aws`, keyed off `var.egress_allowlist`.
- A private endpoint path for the model artifact store (Azure Storage
  private endpoint), analogous to the AWS module's S3 gateway endpoint.

Build and validate against a real Azure subscription as its own scoped
piece of work rather than guessing at Azure quirks inside another
deliverable.
