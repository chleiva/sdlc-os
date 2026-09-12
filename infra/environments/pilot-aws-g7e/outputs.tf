output "cluster_name" {
  value       = aws_eks_cluster.this.name
  description = "EKS cluster name — used by `aws eks update-kubeconfig` and by bootstrap.sh's smoke-run step."
}

output "cluster_endpoint" {
  value       = aws_eks_cluster.this.endpoint
  description = "EKS API server endpoint."
}

output "region" {
  value       = var.region
  description = "Region this environment landed in (the discovery step's chosen value, echoed back for the operator's records)."
}

output "model_serving_endpoint" {
  value = module.model_serving.service_endpoint
}

output "orchestrator_namespace" {
  value = module.orchestrator.namespace
}

output "grafana_service" {
  value = module.observability.grafana_service
}

output "secret_ids" {
  value       = module.secrets.secret_ids
  description = "Map of logical secret name -> AWS Secrets Manager ARN. Contains no secret values."
}

output "ollama_service_endpoint" {
  value       = var.ollama_enabled ? module.model_serving_ollama[0].service_endpoint : null
  description = "In-cluster DNS name of the Ollama service, or null when ollama_enabled = false. OpenAI-compatible routes live under /v1/ on this endpoint."
}

output "ollama_model_cache_role_arn" {
  value       = local.ollama_model_cache_s3_enabled ? aws_iam_role.ollama_model_cache[0].arn : null
  description = "IRSA role ARN for the Ollama tier's model-cache-restore init container, or null when ollama_model_cache_s3_uri is unset. See modules/model-serving-ollama/README.md \"Restoring the model cache from S3\"."
}
