output "node_pool_name" {
  value       = local.not_implemented
  description = "STUB: not implemented."
}

output "node_role_arn" {
  value       = local.not_implemented
  description = "STUB: not implemented."
}

output "security_group_id" {
  value       = var.egress_allowlist_group_id
  description = "Pass-through of the network module's egress-allowlist group id."
}
