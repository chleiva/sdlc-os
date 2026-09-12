output "namespace" {
  value       = kubernetes_namespace_v1.this.metadata[0].name
  description = "Namespace the observability stack was deployed into."
}

output "grafana_service" {
  value       = "kube-prometheus-stack-grafana.${kubernetes_namespace_v1.this.metadata[0].name}.svc.cluster.local"
  description = "In-cluster DNS name of the Grafana service."
}

output "prometheus_service" {
  value       = "kube-prometheus-stack-prometheus.${kubernetes_namespace_v1.this.metadata[0].name}.svc.cluster.local:9090"
  description = "In-cluster DNS name of the Prometheus service, for the smoke run to query against."
}

output "loki_release" {
  value = helm_release.loki.name
}

output "tempo_release" {
  value = helm_release.tempo.name
}

output "external_secret_name" {
  value       = var.external_secrets_enabled ? "${var.grafana_admin_password_secret_name}-sync" : null
  description = "Name of the ExternalSecret syncing the Grafana admin password, or null when external_secrets_enabled = false."
}
