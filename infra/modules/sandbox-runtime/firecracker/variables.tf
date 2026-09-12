# sandbox-runtime/{firecracker,kata,gvisor} — isolation tiers (spec §10.1).
# Every variant declares the same variables/outputs so environments/* can
# select an isolation tier without the orchestrator module knowing which
# one is in use beyond the RuntimeClass name it references.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "node_pool_name" {
  type        = string
  description = "GPU node pool name this sandbox runtime is installed onto (must match the containerd shim's node bootstrap)."
}

variable "runtime_class_name" {
  type        = string
  description = "Name of the Kubernetes RuntimeClass this module registers. Sandboxed pods (agent tool execution, per spec §10.1) set runtimeClassName to this."
  default     = "sdlc-auto-sandbox"
}

variable "tags" {
  type        = map(string)
  description = "Common resource labels applied to every resource this module creates."
  default     = {}
}
