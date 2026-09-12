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
  description = "Static replica count used ONLY when keda_enabled = false (running this tier without autoscaling at all). When keda_enabled = true, main.tf ignores this and passes the chart a base replica count of 0 -- the KEDA ScaledObject below (minReplicaCount = 0) is solely responsible for scaling 0->1 in response to real demand and back to 0 when idle; a nonzero Helm-managed base value would otherwise schedule a pod (and therefore launch a real spot GPU instance via Karpenter) on every `tofu apply`/`helm upgrade`, regardless of whether any actual work had triggered it -- and with no way back to 0 at all if KEDA weren't there to take over. Single-cell, no GPU sharing per the platform's design -- 1 is the only sane value for the keda_enabled = false case, matching the ScaledObject's maxReplicaCount."
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

# --- Model-cache restore from S3 (replaces "pull from Ollama's public
# registry on every cold start" with "restore from S3, pull only as a
# fallback") -- see README.md "Restoring the model cache from S3" for
# the full behavior, the one-time seeding step, and the S3-vs-EBS cost/
# zonal-lock reasoning.

variable "model_cache_s3_uri" {
  type        = string
  description = <<-EOT
    Optional s3://bucket/prefix Ollama's model-cache-restore init
    container syncs (`aws s3 sync`) into a shared `emptyDir` volume on
    pod start, before the existing `postStart: ollama pull` step runs as
    a safety-net fallback (kept unconditionally -- `ollama pull` is a
    no-op once the model is already on disk, so this is never an
    either/or). Null (the default): feature off -- no init container, no
    ServiceAccount rendered, this tier's cold-start behavior is exactly
    what it was before this variable existed (fresh `ollama pull` from
    Ollama's public registry on every pod start). This module does not
    create the S3 bucket -- see README's "what a human must still do".
  EOT
  default     = null
}

variable "service_account_role_arn" {
  type        = string
  description = <<-EOT
    IRSA role ARN to annotate the Ollama pod's ServiceAccount with
    (`eks.amazonaws.com/role-arn`), so the model-cache-restore init
    container's `aws s3 sync` call has real AWS credentials scoped to
    exactly the configured bucket/prefix -- same IRSA-annotation idiom as
    modules/observability's `eso_service_account_role_arn` (see that
    module's `kubernetes_service_account_v1.eso_grafana`). Null (the
    default): no ServiceAccount is rendered at all and the Deployment
    runs under the namespace's default ServiceAccount -- fine when
    `model_cache_s3_uri` is also null, but required (enforced via a
    `precondition` on `helm_release.ollama`, see main.tf) when it is set.
  EOT
  default     = null
}
