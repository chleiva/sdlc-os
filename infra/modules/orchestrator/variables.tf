# orchestrator — K8s control plane, agent runtime, MCP servers (spec
# §14.3). F1's scope is the substrate this runs on and the Helm/manifest
# skeleton that deploys it (namespace, RBAC, network policies, service
# mesh mTLS wiring) — NOT the agent runtime logic itself, which is D2's
# deliverable. The chart in ./chart is intentionally a skeleton: it stands
# up the namespace, service accounts, NetworkPolicies, and a placeholder
# Deployment so bootstrap.sh's step 4 and step 5 (smoke run) have
# something real to apply and probe; D2 replaces the placeholder
# container image with the actual orchestrator build.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "namespace" {
  type        = string
  description = "Kubernetes namespace for the orchestrator + MCP servers."
  default     = "orchestrator"
}

variable "model_serving_endpoint" {
  type        = string
  description = "In-cluster endpoint from the model-serving module's `service_endpoint` output."
}

variable "mesh_enabled" {
  type        = bool
  description = "Whether to install the service-mesh (mTLS) control plane alongside the orchestrator namespace (spec §14.5, §7.5). Disabled by default so a bare pilot bring-up isn't blocked on a mesh CNI choice; the pilot environment composition turns it on explicitly."
  default     = false
}

variable "mesh_provider" {
  type        = string
  description = "Which service mesh to install when mesh_enabled is true. \"linkerd\" is the reference choice (lighter control plane, simpler mTLS-by-default posture than Istio for a single-cluster pilot)."
  default     = "linkerd"

  validation {
    condition     = contains(["linkerd", "istio"], var.mesh_provider)
    error_message = "mesh_provider must be \"linkerd\" or \"istio\"."
  }
}

variable "checkpoint_store_endpoint" {
  type        = string
  description = "Durable-execution checkpoint store endpoint (spec §16.1, §9.3) the orchestrator writes run state to. F1 does not implement the store — this only wires the connection string through as a namespaced ConfigMap entry."
  default     = ""
}

variable "termination_grace_period_seconds" {
  type        = number
  description = "Sized against spot-lifecycle's warning_window_seconds so an in-progress run gets a forced checkpoint write (spec §14.8 step 5) before SIGKILL."
  default     = 100
}

variable "tenant_id" {
  type        = string
  description = "Optional tenant scope (spec §14.13) — per §14.11 (Rev 8), the orchestrator runs inside the tenant's own compute cell, not on the shared control plane. Null for the shared Phase 0 pilot."
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Common resource labels applied to every resource this module creates."
  default     = {}
}
