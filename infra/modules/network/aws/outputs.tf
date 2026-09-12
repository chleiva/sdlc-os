# Module contract outputs — identical shape across network/{aws,gcp,azure,baremetal}.
# A composition under environments/ reads only these, never an aws_*-specific
# resource attribute directly, so the module is swappable per spec §14.3/§14.10.

output "network_id" {
  value       = aws_vpc.this.id
  description = "Cloud-native identifier of the network (VPC id on AWS)."
}

output "public_subnet_ids" {
  value       = aws_subnet.public[*].id
  description = "Subnet ids for public/ingress-facing resources (NAT, load balancers)."
}

output "private_subnet_ids" {
  value       = aws_subnet.private[*].id
  description = "Subnet ids for the control plane (orchestrator, MCP servers, Run Registry)."
}

output "gpu_subnet_ids" {
  value       = aws_subnet.gpu[*].id
  description = "Subnet ids for the GPU node pool and sandbox-runtime layer."
}

output "egress_allowlist_group_id" {
  value       = aws_security_group.gpu_egress_allowlist.id
  description = "Security group / firewall policy id enforcing the GPU/sandbox egress allowlist (spec §10.1, §14.5)."
}
