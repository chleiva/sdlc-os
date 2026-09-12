output "node_pool_name" {
  value       = local.name
  description = "Name of the Karpenter NodePool (and matching EC2NodeClass) this module created."
}

output "node_role_arn" {
  value       = aws_iam_role.node.arn
  description = "IAM role ARN assumed by nodes launched into this pool."
}

output "security_group_id" {
  value       = var.egress_allowlist_group_id
  description = "Security group id attached to nodes in this pool (pass-through of the network module's egress-allowlist group)."
}

output "instance_profile_name" {
  value       = aws_iam_instance_profile.node.name
  description = "Instance profile name backing node_role_arn (AWS-specific; not part of the cross-cloud contract but exposed for debugging)."
}
