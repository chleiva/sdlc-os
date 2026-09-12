variable "environment" {
  type        = string
  description = "Environment name — fixed for this composition."
  default     = "prod-aws"
}

variable "region" {
  type        = string
  description = <<-EOT
    AWS region. Left with NO default deliberately: bootstrap.sh's
    pre-flight discovery step (spec §14.8) queries live spot
    price/interruption-frequency across var.candidate_regions and passes
    the winning region in via -var at apply time. A committed default
    here would be exactly the "region pinned once in a Terraform
    variable" spec §14.8 says never to do.
  EOT
}

variable "candidate_regions" {
  type        = list(string)
  description = "Region candidate set the discovery step is allowed to choose from (spec §14.8). Not a hardcoded single region — a search space."
  default     = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-south-2"]
}

variable "instance_types" {
  type        = list(string)
  description = "GPU instance type candidates for the pool (spec §13.3 ladder). Production uses the full ladder so capacity-optimized allocation (spec §14.8) has a real candidate set to diversify across, not a single size."
  default     = ["g7e.2xlarge", "g7e.12xlarge", "g7e.24xlarge", "g7e.48xlarge"]
}

variable "az_count" {
  type        = number
  description = "Number of availability zones the network and GPU node pool span. Wider than the pilot's default of 2 for production redundancy."
  default     = 3
}

variable "node_pool_min_size" {
  type    = number
  default = 1
}

variable "node_pool_max_size" {
  type    = number
  default = 6
}

variable "node_pool_desired_size" {
  type    = number
  default = 2
}

variable "max_interruption_frequency" {
  type        = string
  description = "Maximum acceptable spot interruption-frequency rating the discovery step will accept (spec §14.8)."
  default     = "medium"
}

variable "egress_allowlist" {
  type        = list(string)
  description = "CIDR ranges the GPU/sandbox subnets may reach on egress (spec §10.1, §14.5). Pilot default is empty — deny-all beyond the module's own VPC endpoints — until a real allowlist (artifact store, Jira/GitHub API ranges) is scoped."
  default     = []
}

variable "model_artifact_uri" {
  type        = string
  description = "Pinned model artifact location in the organization's own artifact store (spec §13.5). No default — must be supplied per deployment, never a public host URL."
}

variable "model_weight_checksum" {
  type        = string
  description = "Checksum the pinned artifact must match (spec §13.5)."
}

variable "secret_names" {
  type        = list(string)
  description = "Logical secret names to provision empty containers for (values bootstrapped out-of-band by bootstrap.sh step 3)."
  default = [
    "jira-webhook-hmac-key",
    "github-app-private-key",
    "model-artifact-store-credentials",
    "grafana-admin-password",
  ]
}

variable "storage_class" {
  type        = string
  description = "Kubernetes StorageClass for observability persistent volumes."
  default     = "gp3"
}

variable "mesh_enabled" {
  type        = bool
  description = "Install the service mesh (mTLS) control plane (spec §14.5). On by default for the pilot so the portability/mTLS posture is exercised from day one, not deferred."
  default     = true
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags."
  default = {
    "sdlc-auto:managed-by" = "opentofu"
    "sdlc-auto:phase"      = "0"
  }
}
