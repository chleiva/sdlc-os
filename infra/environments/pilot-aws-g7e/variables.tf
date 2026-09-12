variable "environment" {
  type        = string
  description = "Environment name — fixed for this composition."
  default     = "pilot-aws-g7e"
}

variable "region" {
  type        = string
  description = <<-EOT
    AWS region. Left with NO default deliberately: bootstrap.sh's
    pre-flight discovery step (spec §14.8) queries live spot
    price/interruption-frequency across var.candidate_regions and passes
    the winning region in via -var at apply time. A committed default
    here would be exactly the "region pinned once in a Terraform
    variable" spec §14.8 says never to do.
  EOT
}

variable "candidate_regions" {
  type        = list(string)
  description = "Region candidate set the discovery step is allowed to choose from (spec §14.8). Not a hardcoded single region — a search space."
  default     = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-south-2"]
}

variable "instance_types" {
  type        = list(string)
  description = "GPU instance type candidates for the pilot pool (spec §13.3 ladder). Phase 0 pilot is a single g7e.2xlarge."
  default     = ["g7e.2xlarge"]
}

variable "max_interruption_frequency" {
  type        = string
  description = "Maximum acceptable spot interruption-frequency rating the discovery step will accept (spec §14.8)."
  default     = "medium"
}

variable "egress_allowlist" {
  type        = list(string)
  description = "CIDR ranges the GPU/sandbox subnets may reach on egress (spec §10.1, §14.5). Pilot default is empty — deny-all beyond the module's own VPC endpoints — until a real allowlist (artifact store, Jira/GitHub API ranges) is scoped."
  default     = []
}

variable "model_artifact_uri" {
  type        = string
  description = "Pinned model artifact location in the organization's own artifact store (spec §13.5). No default — must be supplied per deployment, never a public host URL."
}

variable "model_weight_checksum" {
  type        = string
  description = "Checksum the pinned artifact must match (spec §13.5)."
}

variable "secret_names" {
  type        = list(string)
  description = "Logical secret names to provision empty containers for (values bootstrapped out-of-band by bootstrap.sh step 3)."
  default = [
    "jira-webhook-hmac-key",
    "github-app-private-key",
    "model-artifact-store-credentials",
    "grafana-admin-password",
  ]
}

variable "storage_class" {
  type        = string
  description = "Kubernetes StorageClass for observability persistent volumes."
  default     = "gp3"
}

variable "mesh_enabled" {
  type        = bool
  description = "Install the service mesh (mTLS) control plane (spec §14.5). On by default for the pilot so the portability/mTLS posture is exercised from day one, not deferred."
  default     = true
}

# --- Ollama model-serving option (alongside vLLM, not a replacement) ------
#
# See modules/model-serving-ollama/README.md for the full real-vs-gap
# breakdown (model pull-on-every-start cost, context-length/VRAM caveat,
# KEDA/run-registry trigger gap). Off by default: the vLLM path
# (module.model_serving below) remains this environment's default serving
# backend; a real pilot that wants to compare both sets this true.

variable "ollama_enabled" {
  type        = bool
  description = "Deploy the Ollama serving tier (modules/model-serving-ollama) alongside the existing vLLM tier. False by default -- opt-in, not a replacement for the vLLM path."
  default     = false
}

variable "ollama_model_name" {
  type        = string
  description = "Model for the Ollama tier to `ollama pull` on first pod start (spec §13.5's pinned model)."
  default     = "ornith-1.5-35b-a3b"
}

variable "ollama_instance_type" {
  type        = string
  description = "Single-GPU instance type for the Ollama tier's dedicated spot-only NodePool and pod nodeSelector -- a 48GB-class card. Parameterized, not hardcoded; g6e.xlarge (NVIDIA L40S) is the documented default."
  default     = "g6e.xlarge"
}

variable "ollama_consolidate_after" {
  type        = string
  description = "Karpenter `disruption.consolidateAfter` for the Ollama tier's spot-only NodePool -- short by design (spec: scale-to-zero should actually reclaim the node quickly once KEDA scales the Deployment to 0)."
  default     = "60s"
}

variable "ollama_keda_enabled" {
  type        = bool
  description = <<-EOT
    Whether to declare the KEDA ScaledObject for the Ollama tier. False
    by default: see modules/model-serving-ollama/README.md's "KEDA
    trigger: run-registry integration gap" -- the metrics-api endpoint
    this trigger needs does not exist in services/run-registry today, so
    turning this on without a real `ollama_run_registry_metrics_url`
    would point KEDA at nothing. Requires KEDA's controller already
    installed cluster-wide regardless (see that module's README).
  EOT
  default     = false
}

variable "ollama_run_registry_metrics_url" {
  type        = string
  description = "URL of a run-registry HTTP endpoint returning {\"count\": <int>} of runs awaiting a model response (see modules/model-serving-ollama/README.md for the exact contract). No real default -- this endpoint does not exist in services/run-registry today; must be set before ollama_keda_enabled = true is usable."
  default     = ""
}

variable "ollama_model_cache_s3_uri" {
  type        = string
  description = <<-EOT
    Optional s3://bucket/prefix (e.g.
    "s3://my-org-ollama-cache/ornith-1.5-35b-a3b/") to restore the Ollama
    tier's on-disk model store from on pod start, via a real `aws s3
    sync` init container, instead of a fresh ~23GB `ollama pull` from
    Ollama's public registry on every cold start -- see
    modules/model-serving-ollama/README.md "Restoring the model cache
    from S3" for the full behavior, the one-time seeding step, and why
    S3 (not an EBS-backed PVC) was chosen for this specific spot/
    scale-to-zero tier. Null (the default): feature off -- this tier's
    cold-start behavior is unchanged. Only takes effect when
    `ollama_enabled = true`; when set, eks.tf declares a real
    least-privilege IRSA role/policy scoped to exactly this
    bucket/prefix and wires its ARN into
    `module.model_serving_ollama.service_account_role_arn`. This
    environment does NOT provision the S3 bucket itself -- see that same
    README section for what a human still needs to do.
  EOT
  default     = null
}

variable "tags" {
  type        = map(string)
  description = "Common resource tags."
  default = {
    "sdlc-auto:managed-by" = "opentofu"
    "sdlc-auto:phase"      = "0"
  }
}
