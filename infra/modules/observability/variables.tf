# observability — Prometheus, Grafana, Loki, Tempo, cloud-agnostic (spec
# §14.3, §16.4). No per-cloud variant: this deploys via Helm only.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "namespace" {
  type        = string
  description = "Kubernetes namespace for the observability stack."
  default     = "observability"
}

variable "metrics_retention" {
  type        = string
  description = "Prometheus metrics retention window (e.g. \"15d\")."
  default     = "15d"
}

variable "storage_class" {
  type        = string
  description = "StorageClass for Prometheus/Loki/Tempo persistent volumes. Left to the environment composition since it is cluster/cloud-specific (e.g. gp3 on AWS EBS CSI)."
}

variable "grafana_admin_password_secret_name" {
  type        = string
  description = <<-EOT
    Logical name of the secret (from the secrets module's `secret_ids`
    output) holding the Grafana admin password. The password VALUE is
    never a variable here (spec §14.6) — only its secret-store reference,
    injected into the Grafana pod at runtime via a secret volume/env-from,
    never through a tofu resource argument.
  EOT
}

variable "tags" {
  type        = map(string)
  description = "Common resource labels applied to every resource this module creates."
  default     = {}
}
