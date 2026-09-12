# model-serving-ollama — cloud-agnostic: Ollama + the pinned model,
# spot-only/scale-to-zero variant of model-serving. See README.md for why
# this is a sibling module to modules/model-serving rather than a
# `serving_backend` switch inside it.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"pilot-aws-g7e\"."
}

variable "namespace" {
  type        = string
  description = <<-EOT
    Kubernetes namespace to deploy into. Defaults to a distinct
    namespace from modules/model-serving's ("model-serving") so the two
    can be composed side by side in the same environment (spec'd use
    case: a pilot comparing serving backends) without either module's
    `kubernetes_namespace_v1.this` resource colliding on the same
    namespace name.
  EOT
  default     = "model-serving-ollama"
}

variable "model_name" {
  type        = string
  description = "Model to `ollama pull` on first pod start (spec §13.5's pinned model)."
  default     = "ornith-1.5-35b-a3b"
}

variable "image_tag" {
  type        = string
  description = "ollama/ollama image tag. \"latest\" by default for the pilot; pin an exact tag/digest for anything beyond a comparison pilot, same caveat as modules/model-serving's vLLM image tag."
  default     = "latest"
}

variable "replicas" {
  type        = number
  description = "Number of Ollama replicas. Single-cell, no GPU sharing per the platform's design -- 1 is the only sane value alongside the KEDA ScaledObject's maxReplicaCount below; not enforced here since `replicas` only sets the Helm chart's *base* value (KEDA overrides the live replica count once `keda_enabled = true`, per KEDA's own HPA-behind-the-scenes mechanics)."
  default     = 1
}

variable "node_pool_name" {
  type        = string
  description = "GPU node pool name (from gpu-node-pool/aws's `node_pool_name` output for the spot-only Ollama tier instance -- see environments/pilot-aws-g7e/main.tf) to schedule onto; the deployment tolerates that pool's taints."
}

variable "instance_type" {
  type        = string
  description = <<-EOT
    Single-GPU instance type this tier targets, added as an extra
    `node.kubernetes.io/instance-type` nodeSelector alongside
    `node_pool_name` (belt-and-suspenders: the real constraint is the
    NodePool's own `instance_types` list in gpu-node-pool/aws -- this is
    a redundant, explicit pin at the pod level so a scheduling mismatch
    surfaces immediately rather than only showing up as "wrong instance
    happened to win Karpenter's placement"). Default matches a 48GB-class
    single-GPU card (L40S): g6e.xlarge (4 vCPU / 32GiB RAM / 1x L40S).
    Parameterized, not hardcoded, so a different 48GB-class profile (or a
    larger card, if headroom for a bigger model/context is ever needed)
    is a variable change, not a chart edit.
  EOT
  default     = "g6e.xlarge"
}

variable "gpu_count" {
  type        = number
  description = "GPUs requested/limited per pod. Fixed at 1 by the platform's single-cell, no-GPU-sharing design (see README) -- exposed as a variable only so a future multi-GPU instance profile doesn't require a chart edit, not because >1 is currently a supported configuration."
  default     = 1
}

variable "cpu_request" {
  type        = string
  description = "Pod CPU request. Default sized under g6e.xlarge's 4 vCPUs."
  default     = "3"
}

variable "cpu_limit" {
  type        = string
  description = "Pod CPU limit."
  default     = "4"
}

variable "memory_request" {
  type        = string
  description = "Pod memory request. Default sized under g6e.xlarge's 32GiB RAM."
  default     = "24Gi"
}

variable "memory_limit" {
  type        = string
  description = "Pod memory limit."
  default     = "30Gi"
}

variable "termination_grace_period_seconds" {
  type        = number
  description = "Plain kubelet SIGTERM grace period -- see chart's deployment.yaml comment for why this tier has no sized drain-step budget the way modules/model-serving's vLLM chart does."
  default     = 30
}

variable "tenant_id" {
  type        = string
  description = "Optional tenant scope (spec §14.13). Null for the shared Phase 0 pilot deployment."
  default     = null
}

variable "keda_enabled" {
  type        = bool
  description = <<-EOT
    Whether to declare the KEDA `ScaledObject` that drives this tier's
    scale-to-zero behavior. Default false: a `kubernetes_manifest` for a
    CRD KEDA's controller defines will fail at `tofu plan`/`apply` time
    against any cluster where the KEDA controller (assumed already
    installed cluster-wide -- see README) isn't actually running yet,
    so this is opt-in rather than always-on.
  EOT
  default     = false
}

variable "keda_metrics_api_url" {
  type        = string
  description = <<-EOT
    URL KEDA's `metrics-api` trigger polls for a count of runs currently
    awaiting a model response from this tier (spec'd trigger signal).
    See README "KEDA trigger: run-registry integration gap" for the
    exact expected request/response contract and this deliverable's
    explicit finding that run-registry does not expose this endpoint
    today. Required (validated via a `precondition`, not a variable
    validation block, to stay on OpenTofu >= 1.6) when `keda_enabled =
    true`.
  EOT
  default     = ""
}

variable "keda_polling_interval_seconds" {
  type        = number
  description = "KEDA's `pollingInterval` -- how often the metrics-api trigger is queried."
  default     = 30
}

variable "keda_cooldown_period_seconds" {
  type        = number
  description = "KEDA's `cooldownPeriod` -- how long the metric must read below target before scaling back down to 0, avoiding thrashing on a momentary dip."
  default     = 300
}

variable "tags" {
  type        = map(string)
  description = "Common resource labels applied to every resource this module creates."
  default     = {}
}
