# Module contract outputs — identical shape across network/{aws,gcp,azure,baremetal}.
# Stub values only; see main.tf and README.md. A real apply of this module
# is not possible until the Phase 1.5 implementation lands.

output "network_id" {
  value       = local.not_implemented
  description = "Cloud-native identifier of the network. STUB: not implemented."
}

output "public_subnet_ids" {
  value       = []
  description = "Subnet ids for public/ingress-facing resources. STUB: not implemented."
}

output "private_subnet_ids" {
  value       = []
  description = "Subnet ids for the control plane. STUB: not implemented."
}

output "gpu_subnet_ids" {
  value       = []
  description = "Subnet ids for the GPU node pool and sandbox-runtime layer. STUB: not implemented."
}

output "egress_allowlist_group_id" {
  value       = local.not_implemented
  description = "Firewall policy id enforcing the GPU/sandbox egress allowlist. STUB: not implemented."
}
