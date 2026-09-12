output "namespace" {
  value       = kubernetes_namespace_v1.this.metadata[0].name
  description = "Namespace the orchestrator skeleton was deployed into."
}

output "service_account_name" {
  value       = kubernetes_service_account_v1.orchestrator.metadata[0].name
  description = "Service account D2's orchestrator build runs as; IAM/workload-identity bindings attach here."
}

output "release_name" {
  value       = helm_release.orchestrator.name
  description = "Helm release name, for the smoke-run step to check rollout status against."
}

output "mesh_installed" {
  value       = var.mesh_enabled
  description = "Whether the service mesh control plane was installed by this module invocation."
}
