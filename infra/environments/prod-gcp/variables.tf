variable "environment" {
  type        = string
  description = "Environment name — fixed for this composition."
  default     = "prod-gcp"
}

variable "project_id" {
  type        = string
  description = "GCP project id. No default — this composition is a portability-proof structure, not yet apply-tested (see README.md)."
}

variable "region" {
  type        = string
  description = "GCP region. Left with no default for the same live-discovery reason as the AWS compositions' `region` variable (spec §14.8) — not yet wired to a real discovery step for GCP in this pass."
}

variable "egress_allowlist" {
  type        = list(string)
  description = "CIDR ranges the GPU/sandbox subnets may reach on egress (spec §10.1, §14.5)."
  default     = []
}

variable "instance_types" {
  type        = list(string)
  description = "GPU instance type candidates (spec §13.3-equivalent GCP shapes). Placeholder values — network/gcp and gpu-node-pool/gcp are stubs that do not yet validate these against real GCP machine types."
  default     = ["a2-ultragpu-1g"]
}

variable "model_artifact_uri" {
  type        = string
  description = "Pinned model artifact location (spec §13.5)."
}

variable "model_weight_checksum" {
  type        = string
  description = "Checksum the pinned artifact must match (spec §13.5)."
}

variable "secret_names" {
  type        = list(string)
  description = "Logical secret names (values bootstrapped out-of-band)."
  default = [
    "jira-webhook-hmac-key",
    "github-app-private-key",
    "model-artifact-store-credentials",
    "grafana-admin-password",
  ]
}

variable "storage_class" {
  type        = string
  description = "Kubernetes StorageClass for observability persistent volumes (GKE default: \"standard-rwo\")."
  default     = "standard-rwo"
}

variable "mesh_enabled" {
  type    = bool
  default = true
}

variable "tags" {
  type        = map(string)
  description = "Common resource labels."
  default = {
    "sdlc-auto:managed-by" = "opentofu"
    "sdlc-auto:phase"      = "1.5-structure-only"
  }
}
