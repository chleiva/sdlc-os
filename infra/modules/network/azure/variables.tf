# Module contract: network/{aws,gcp,azure,baremetal} — see network/aws/variables.tf
# for the authoritative description of each variable. This file MUST declare
# the identical variable set; only defaults/descriptions may be adapted.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\". Used for resource naming/tagging only."
}

variable "region" {
  type        = string
  description = "Primary cloud region for this network. Live-discovered by bootstrap, never hardcoded."
}

variable "cidr_block" {
  type        = string
  description = "CIDR block for the network."
  default     = "10.60.0.0/16"
}

variable "az_count" {
  type        = number
  description = "Number of availability zones to spread subnets across."
  default     = 2
}

variable "egress_allowlist" {
  type        = list(string)
  description = "CIDR ranges the GPU/sandbox subnets are permitted to reach on egress (spec §10.1, §14.5)."
  default     = []
}

variable "tenant_id" {
  type        = string
  description = "Optional tenant scope for a per-tenant compute cell network (spec §14.13). Null = shared."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags/labels applied to every resource this module creates."
  default     = {}
}
