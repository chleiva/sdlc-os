output "namespace" {
  value       = kubernetes_namespace_v1.this.metadata[0].name
  description = "Namespace the model-serving Helm release was deployed into."
}

output "service_endpoint" {
  value       = "vllm-${var.environment}.${kubernetes_namespace_v1.this.metadata[0].name}.svc.cluster.local:8000"
  description = "In-cluster DNS name of the vLLM service, for the orchestrator module to point its model client at."
}

output "release_name" {
  value       = helm_release.vllm.name
  description = "Helm release name, for the smoke-run step to check rollout status against."
}
