output "tenant_environment" {
  value       = local.tenant_environment
  description = "The per-tenant environment name this cell's resources were derived from (matches tenant_cell.naming.tenant_environment() in the Python control-plane)."
}

output "node_pool_name" {
  value       = module.gpu_node_pool.node_pool_name
  description = "This tenant's Karpenter NodePool name -- structurally distinct from every other tenant's, by construction (see main.tf's local.tenant_environment)."
}

output "node_role_arn" {
  value       = module.gpu_node_pool.node_role_arn
  description = "IAM role ARN for nodes in this tenant's pool."
}

output "primary_model_endpoint" {
  value       = module.model_serving_primary.service_endpoint
  description = "In-cluster DNS name of this tenant's primary (implementer) model's vLLM service."
}

output "reviewer_model_endpoint" {
  value       = length(module.model_serving_reviewer) > 0 ? module.model_serving_reviewer[0].service_endpoint : null
  description = "In-cluster DNS name of this tenant's reviewer model's vLLM service, if a second self-hosted model was configured; null when this tenant instead uses a frontier-API escalation path."
}

output "namespace" {
  value       = module.model_serving_primary.namespace
  description = "Kubernetes namespace this tenant's model-serving workloads run in."
}
