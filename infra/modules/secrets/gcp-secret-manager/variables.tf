# Module contract: secrets/{aws-secrets-manager,gcp-secret-manager,azure-key-vault,vault}
#
# This module NEVER accepts a secret value as a variable and never writes
# one to a resource body that OpenTofu would persist to state (spec §14.6:
# "secrets are never written into Terraform/OpenTofu variables or state").
# It only provisions the *containers* (and access policy) that
# bootstrap.sh's step 3 (secret bootstrap) then populates out-of-band via
# a direct API/CLI call, never through `tofu apply`.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "secret_names" {
  type        = list(string)
  description = <<-EOT
    Logical names of secrets to provision empty containers for (e.g.
    "jira-webhook-hmac-key", "github-app-private-key",
    "model-artifact-store-credentials"). No values here — only names.
  EOT
}

variable "reader_principal_arns" {
  type        = list(string)
  description = "IAM roles / service-account principals permitted to read these secrets at runtime (e.g. the orchestrator's and MCP servers' pod IRSA roles)."
  default     = []
}

variable "kms_key_arn" {
  type        = string
  description = "Optional customer-managed key to encrypt secrets with. Null uses the provider's default encryption."
  default     = null
}

variable "tenant_id" {
  type        = string
  description = "Optional tenant scope for a per-tenant compute cell's secrets (spec §14.13). Null for the shared control plane."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags applied to every resource this module creates."
  default     = {}
}
