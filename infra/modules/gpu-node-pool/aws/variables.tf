# Module contract: gpu-node-pool/{aws,gcp,azure,baremetal}
# Every variant declares exactly these variables and outputs.tf's outputs.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "cluster_name" {
  type        = string
  description = "Name of the Kubernetes cluster this node pool joins (EKS cluster name on AWS)."
}

variable "network_id" {
  type        = string
  description = "Network id from the network module's `network_id` output."
}

variable "gpu_subnet_ids" {
  type        = list(string)
  description = "Subnet ids from the network module's `gpu_subnet_ids` output."
}

variable "egress_allowlist_group_id" {
  type        = string
  description = "Security-group/firewall id from the network module's `egress_allowlist_group_id` output, attached to every node this pool launches."
}

variable "instance_types" {
  type        = list(string)
  description = <<-EOT
    Candidate instance types for this pool, in priority order, drawn from
    the sizing ladder (spec §13.3). For pilot-aws-g7e this is just
    ["g7e.2xlarge"]; production environments list the full ladder
    (g7e.2xlarge, g7e.12xlarge, g7e.24xlarge, g7e.48xlarge) plus any
    same-capability fallback family (spec §14.8) so Karpenter's
    capacity-optimized allocation has a real candidate set to diversify
    across.
  EOT
  default     = ["g7e.2xlarge"]
}

variable "capacity_type" {
  type        = string
  description = "Primary capacity type: \"spot\" (default posture per spec §14.1/§14.8) or \"on-demand\"."
  default     = "spot"

  validation {
    condition     = contains(["spot", "on-demand"], var.capacity_type)
    error_message = "capacity_type must be \"spot\" or \"on-demand\"."
  }
}

variable "on_demand_fallback" {
  type        = bool
  description = "Whether the pool automatically falls back to on-demand when no spot pool clears the interruption-frequency threshold (spec §14.8). Always true in the reference deployment; exposed for test environments that want to force a specific behavior."
  default     = true
}

variable "max_interruption_frequency" {
  type        = string
  description = "Maximum acceptable spot interruption-frequency rating (\"low\"|\"medium\"|\"high\"|\"very-high\", AWS Spot Placement Score-style scale) a candidate pool must clear to be selected by the live discovery step (spec §14.8). Never used to hardcode a region/pool — only as a filter over live-queried candidates."
  default     = "medium"
}

variable "min_size" {
  type        = number
  description = "Minimum node count for this pool."
  default     = 0
}

variable "max_size" {
  type        = number
  description = "Maximum node count for this pool."
  default     = 1
}

variable "desired_size" {
  type        = number
  description = "Starting node count for this pool (Phase 0 pilot: 1)."
  default     = 1
}

variable "taints" {
  type = list(object({
    key    = string
    value  = string
    effect = string
  }))
  description = "Taints applied to every node in this pool so only GPU/sandbox workloads schedule onto it (spec §14.4: \"dedicated, tainted GPU node pool, isolated from general workloads\")."
  default = [{
    key    = "sdlc-auto.io/gpu"
    value  = "true"
    effect = "NoSchedule"
  }]
}

variable "labels" {
  type        = map(string)
  description = "Kubernetes node labels applied to every node in this pool (used by model-serving/orchestrator scheduling and by KEDA/Karpenter selection)."
  default     = { "sdlc-auto.io/node-pool" = "gpu" }
}

variable "tenant_id" {
  type        = string
  description = <<-EOT
    Optional tenant scope for a per-tenant compute cell (spec §14.13).
    Null for the shared Phase 0 pilot pool. This module accepts the
    parameter so D6/F2's tenant-cell provisioning logic can request a
    tenant-scoped pool later; deciding *when* to call it per-tenant is
    explicitly out of scope for this deliverable (see brief).
  EOT
  default     = null
}

variable "pool_name_suffix" {
  type        = string
  description = <<-EOT
    Optional suffix appended to the generated NodePool/EC2NodeClass/IAM
    name ("$${var.environment}-gpu-node-pool[-<suffix>]"). Empty string
    (the default) reproduces this module's original naming exactly, so
    every existing caller of this module is unaffected. Set this when an
    environment composes the module more than once against the same
    `environment` value -- e.g. a second, spot-only/scale-to-zero pool
    for an Ollama model-serving tier alongside the main GPU pool (see
    environments/pilot-aws-g7e/main.tf) -- so the two instances don't
    collide on resource names.
  EOT
  default     = ""

  validation {
    condition     = can(regex("^[a-z0-9-]*$", var.pool_name_suffix))
    error_message = "pool_name_suffix must be empty or lowercase alphanumeric/hyphen only (it becomes part of an AWS/Kubernetes resource name)."
  }
}

variable "consolidate_after" {
  type        = string
  description = <<-EOT
    Karpenter NodePool `disruption.consolidateAfter` duration (e.g.
    "1m", "60s") -- how long a node must sit empty/underutilized before
    Karpenter's `WhenEmptyOrUnderutilized` consolidation actually
    terminates the EC2 instance. Kept short (a spot-only, scale-to-zero
    tier wants the node reclaimed quickly once KEDA has scaled its
    Deployment to zero) or longer (the shared vLLM pool, where
    frequent churn is undesirable), per pool. Defaults to this module's
    original hardcoded value so existing callers see no behavior change.
  EOT
  default     = "1m"
}

variable "user_data" {
  type        = string
  description = <<-EOT
    Rendered node-bootstrap script (EC2 user data) to set on the
    EC2NodeClass, run before kubelet registers the node. Intended to be
    `sandbox-runtime/firecracker`'s (or kata/gvisor's) rendered
    `bootstrap_script` output -- see that module's own header comment
    for why it previously sat unwired. `null` (the default) omits
    `userData` from the EC2NodeClass entirely, reproducing this
    module's original behavior for any caller that doesn't pass one
    (e.g. a pool that never runs sandboxed tool-execution workloads and
    so has no isolation-tier shim to install).
  EOT
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags applied to every resource this module creates."
  default     = {}
}
