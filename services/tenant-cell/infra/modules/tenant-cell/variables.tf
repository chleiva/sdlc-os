# tenant-cell — module contract.
#
# This module owns exactly what wave1-D6's brief scopes to it: calling
# F1's existing gpu-node-pool and model-serving modules per tenant,
# threading tenant_id through both so their outputs (node pool name,
# model-serving service endpoint) are structurally distinct per tenant.
# It does NOT provision a tenant's network, KMS key, or secrets bootstrap
# (spec §14.13 lists those as instantiated by the tenant *onboarding*
# workflow, from F1's existing tenant-parameterized network/secrets
# modules -- out of this deliverable's scope per its own brief) -- it
# consumes an already-existing cluster/network/KMS context as inputs.

variable "base_environment" {
  type        = string
  description = "Base environment name this tenant cell attaches to, e.g. \"pilot-aws-g7e\" (matches an environments/* composition's own var.environment). Combined with tenant_id to derive every per-tenant resource name -- see naming.tf."
}

variable "tenant_id" {
  type        = string
  description = "Tenant identifier (spec §14.13). Required -- there is no \"shared\" default at this layer; a caller that wants the single-tenant Phase 0 shape uses F1's gpu-node-pool/model-serving modules directly (as environments/pilot-aws-g7e/main.tf already does), not this module."

  validation {
    condition     = length(var.tenant_id) > 0
    error_message = "tenant_id must be a non-empty string."
  }
}

# --- passed through to gpu-node-pool (cluster/network context is a
# per-environment singleton or a per-tenant network's output -- this
# module is agnostic to which; see module README) ---------------------

variable "cluster_name" {
  type        = string
  description = "Kubernetes cluster name this tenant cell's node pool joins."
}

variable "network_id" {
  type        = string
  description = "Network id (from the network module's `network_id` output) this tenant cell provisions into."
}

variable "gpu_subnet_ids" {
  type        = list(string)
  description = "Subnet ids (from the network module's `gpu_subnet_ids` output) this tenant cell's GPU node pool launches into."
}

variable "egress_allowlist_group_id" {
  type        = string
  description = "Security-group/firewall id (from the network module's `egress_allowlist_group_id` output) attached to every node this tenant cell's pool launches."
}

# --- sizing ladder (spec §13.3) ---------------------------------------

variable "instance_types" {
  type        = list(string)
  description = "Candidate instance types for this tenant's pool, in priority order (spec §13.3's sizing ladder). Phase-0-equivalent tenants use [\"g7e.2xlarge\"]; a tenant provisioned at a larger tier lists that tier's candidates instead."
  default     = ["g7e.2xlarge"]
}

variable "capacity_type" {
  type        = string
  description = "Primary capacity type for this tenant's pool: \"spot\" (default posture, spec §14.1/§14.8) or \"on-demand\"."
  default     = "spot"
}

variable "on_demand_fallback" {
  type        = bool
  description = "Whether this tenant's pool automatically falls back to on-demand (spec §14.8)."
  default     = true
}

variable "max_interruption_frequency" {
  type        = string
  description = "Maximum acceptable spot interruption-frequency rating this tenant's pool will accept (spec §14.8)."
  default     = "medium"
}

variable "min_size" {
  type        = number
  description = "Minimum node count for this tenant's pool (0 for scale-to-zero, spec §14.11, applied per-tenant per §14.13)."
  default     = 0
}

variable "max_size" {
  type        = number
  description = "Maximum node count for this tenant's pool."
  default     = 1
}

variable "desired_size" {
  type        = number
  description = "Starting node count for this tenant's pool."
  default     = 1
}

# --- primary (implementer) model, served through F1's model-serving --

variable "primary_model_artifact_uri" {
  type        = string
  description = "Pinned primary-model artifact URI in the organization's own artifact store (spec §13.5), scoped to this tenant."
}

variable "primary_model_weight_checksum" {
  type        = string
  description = "Checksum the primary model artifact is pinned to (spec §13.5)."
}

variable "primary_tensor_parallel_size" {
  type        = number
  description = "vLLM --tensor-parallel-size for the primary model on this tenant's sizing tier (spec §13.3: 1 on g7e.2xlarge, 2 on g7e.12xlarge, ...)."
  default     = 1
}

variable "primary_gpu_utilization_target" {
  type        = number
  description = "vLLM --gpu-memory-utilization target for the primary model (spec §13.3 references ~90%)."
  default     = 0.9
}

# --- optional second, architecturally-distinct self-hosted reviewer
# model (spec §13.4/§13.7) -- null on tiers that instead take the
# frontier-API-escalation route (enforced by the control-plane's
# model_diversity module, not by this IaC layer, which only wires the
# second deployment in when a caller supplies one) -------------------

variable "reviewer_model_artifact_uri" {
  type        = string
  description = "Pinned reviewer-model artifact URI, if this tenant hosts a second, architecturally-distinct self-hosted model on the same dedicated hardware (spec §13.4, tiers g7e.12xlarge and up). Null when this tenant instead uses a frontier-API escalation path (no second self-hosted deployment)."
  default     = null
}

variable "reviewer_model_weight_checksum" {
  type        = string
  description = "Checksum the reviewer model artifact is pinned to. Required (non-null) iff reviewer_model_artifact_uri is set."
  default     = null
}

variable "reviewer_tensor_parallel_size" {
  type        = number
  description = "vLLM --tensor-parallel-size for the reviewer model, if hosted."
  default     = 1
}

variable "reviewer_gpu_utilization_target" {
  type        = number
  description = "vLLM --gpu-memory-utilization target for the reviewer model, if hosted."
  default     = 0.9
}

# --- interruption/termination timing (spec §14.8) ---------------------

variable "warning_window_seconds" {
  type        = number
  description = "Expected interruption warning window this tenant's pods size their grace period against (spec §14.8). AWS Spot gives ~120s."
  default     = 120
}

variable "termination_grace_buffer_seconds" {
  type        = number
  description = "How much of warning_window_seconds is reserved for the node-level watcher's own cordon/stop-accepting steps before the pod's own terminationGracePeriodSeconds budget starts (mirrors environments/pilot-aws-g7e/main.tf's `warning_window_seconds - 30` convention)."
  default     = 30
}

variable "replicas" {
  type        = number
  description = "vLLM replica count per model deployment for this tenant. Phase-0-equivalent tenants: 1."
  default     = 1
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags applied to every resource this tenant cell creates, merged with tenant-identifying tags this module adds itself (see main.tf)."
  default     = {}
}
