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
