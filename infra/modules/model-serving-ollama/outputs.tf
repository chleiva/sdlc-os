output "namespace" {
  value       = kubernetes_namespace_v1.this.metadata[0].name
  description = "Namespace the model-serving-ollama Helm release was deployed into."
}

output "service_endpoint" {
  value       = "ollama-${var.environment}.${kubernetes_namespace_v1.this.metadata[0].name}.svc.cluster.local:11434"
  description = "In-cluster DNS name of the Ollama service. OpenAI-compatible routes live under the /v1/ path prefix on this endpoint (e.g. POST http://<this>/v1/chat/completions) -- unlike modules/model-serving's vLLM service, which serves its OpenAI-compatible routes at the endpoint root."
}

output "release_name" {
  value       = helm_release.ollama.name
  description = "Helm release name, for the smoke-run step to check rollout status against."
}

output "keda_scaled_object_name" {
  value       = var.keda_enabled ? "ollama-${var.environment}" : null
  description = "Name of the KEDA ScaledObject this module declares, or null when keda_enabled = false."
}
