# Module contract: network/{aws,gcp,azure,baremetal}
#
# Every cloud variant of this module declares exactly these variables and
# the outputs in outputs.tf, so environments/* can swap one implementation
# for another without touching any module that consumes this one's outputs
# (spec §14.3, §14.10). Do not add an aws-only variable here without adding
# the equivalent (even if a stubbed no-op) to every other variant.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\". Used for resource naming/tagging only."
}

variable "region" {
  type        = string
  description = "Primary cloud region for this network. Discovered/selected live by bootstrap's spot pre-flight step (spec §14.8), never hardcoded in a committed .tfvars."
}

variable "cidr_block" {
  type        = string
  description = "CIDR block for the network (VPC)."
  default     = "10.60.0.0/16"
}

variable "az_count" {
  type        = number
  description = "Number of availability zones to spread subnets across."
  default     = 2
}

variable "egress_allowlist" {
  type        = list(string)
  description = <<-EOT
    CIDR ranges the GPU node pool and sandbox-runtime subnets are permitted
    to reach on egress (spec §10.1, §14.5). Everything not listed here is
    denied at the infrastructure level, independent of which cloud module
    is in use. An empty list means "deny all egress from the GPU/sandbox
    subnets except to the private endpoints this module itself creates."
  EOT
  default     = []
}

variable "tenant_id" {
  type        = string
  description = <<-EOT
    Optional tenant scope for a per-tenant compute cell network (spec
    §14.13). Null for the shared control-plane network / Phase 0 pilot.
    This module accepts the parameter so a future D6/F2-owned caller can
    provision a tenant-scoped network; it does not itself decide when a
    tenant_id is supplied (out of scope per this deliverable's brief).
  EOT
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags/labels applied to every resource this module creates."
  default     = {}
}
