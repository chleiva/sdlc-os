# spot-lifecycle — interruption watcher, cordon/drain hooks (spec §14.8).
#
# Cloud-agnostic in directory layout (spec §14.3 lists it flat, not under
# {aws,gcp,azure,baremetal}), because the *sequence* it enforces — cordon
# -> stop-accepting-requests -> drain-or-fail-to-orchestrator-retry ->
# flush observability -> force checkpoint -- is the same regardless of
# cloud. What differs per cloud is only the interruption *signal source*
# (AWS's ~2-minute Spot interruption notice + EventBridge rebalance-
# recommendation event vs. GCP preemption notice vs. Azure Spot eviction
# notice). This first pass wires the AWS signal path for real (matching
# the aws-only real-build scope of this deliverable) behind a
# `cloud_provider` switch, so a later pass can add the GCP/Azure signal
# sources without changing the cordon/drain/checkpoint sequence itself.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "cloud_provider" {
  type        = string
  description = "Which interruption signal source to wire up: \"aws\" (implemented), \"gcp\"/\"azure\" (not yet implemented — see README.md), \"baremetal\" (no spot concept; this module is a no-op)."
  default     = "aws"

  validation {
    condition     = contains(["aws", "gcp", "azure", "baremetal"], var.cloud_provider)
    error_message = "cloud_provider must be one of: aws, gcp, azure, baremetal."
  }
}

variable "cluster_name" {
  type        = string
  description = "Kubernetes cluster name the watcher DaemonSet runs on."
}

variable "gpu_node_pool_name" {
  type        = string
  description = "Node pool name (from the gpu-node-pool module's `node_pool_name` output) this watcher targets via node selector."
}

variable "checkpoint_webhook_url" {
  type        = string
  description = "In-cluster URL the watcher POSTs to when it must force an immediate checkpoint write on an about-to-be-reclaimed node (spec §14.8 step 5 — the orchestrator's own checkpoint endpoint, spec §9.3/§16.1)."
}

variable "observability_flush_endpoint" {
  type        = string
  description = "In-cluster URL/endpoint the watcher signals to flush buffered logs/metrics/traces off-node before the reclaim lands (spec §14.8 step 4, §16.4)."
}

variable "warning_window_seconds" {
  type        = number
  description = "Expected length of the interruption warning window. AWS Spot gives ~120s; used only as a budget the watcher's own steps are timed against, never assumed exact."
  default     = 120
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags applied to every resource this module creates."
  default     = {}
}
