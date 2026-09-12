# model-serving — cloud-agnostic: vLLM + pinned model, Helm chart (spec
# §14.3). This module deploys the chart in ./chart; it does not decide
# per-tenant provisioning logic (D6/F2's job per this deliverable's
# brief) — it accepts a tenant_id parameter and a node pool to schedule
# onto, and deploys one vLLM deployment against them.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "namespace" {
  type        = string
  description = "Kubernetes namespace to deploy into."
  default     = "model-serving"
}

variable "model_artifact_uri" {
  type        = string
  description = <<-EOT
    URI of the pinned model artifact in the organization's own artifact
    store (spec §13.5, §13.6 — mounted from the org's own store on every
    new node, never re-fetched from a public host). E.g.
    "s3://<org-bucket>/models/ornith-1.5-35b-a3b/nvfp4/<checksum>/".
    No default: this must be set per environment, and it names a
    checksum-pinned artifact, never a mutable "latest" path.
  EOT
}

variable "model_weight_checksum" {
  type        = string
  description = "Checksum the deployed artifact is pinned to (spec §13.5). Surfaced as a pod annotation/label so a drift between the running pod and the intended pin is visible, not silent."
}

variable "tensor_parallel_size" {
  type        = number
  description = "vLLM --tensor-parallel-size. 1 for a single g7e.2xlarge pilot node; 2 for a g7e.12xlarge dual-GPU node (spec §13.3)."
  default     = 1
}

variable "gpu_utilization_target" {
  type        = number
  description = "vLLM --gpu-memory-utilization target (spec §13.3 references ~90%)."
  default     = 0.9
}

variable "replicas" {
  type        = number
  description = "Number of vLLM replicas. Phase 0 pilot: 1."
  default     = 1
}

variable "node_pool_name" {
  type        = string
  description = "GPU node pool name (from gpu-node-pool's `node_pool_name` output) to schedule onto; the deployment tolerates that pool's taints."
}

variable "termination_grace_period_seconds" {
  type        = number
  description = "Sized against spot-lifecycle's `warning_window_seconds` output so an interruption's cordon->drain sequence (spec §14.8) has time to complete a graceful stop-accepting-requests + drain before SIGKILL."
  default     = 90
}

variable "tenant_id" {
  type        = string
  description = "Optional tenant scope (spec §14.13). Null for the shared Phase 0 pilot deployment."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Common resource labels applied to every resource this module creates."
  default     = {}
}
