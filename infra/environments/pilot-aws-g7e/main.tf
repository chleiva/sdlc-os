# pilot-aws-g7e — Phase 0 reference environment (spec §14.3, §21 Phase 0):
# one g7e.2xlarge, one region, on spot capacity with on-demand fallback.
#
# This file composes the module tree; it creates no resources of its own
# beyond wiring module inputs/outputs together. See eks.tf for the EKS
# control plane + system node group these modules attach to.

locals {
  # gpu-node-pool/aws's NodePool/EC2NodeClass/IAM names are a pure
  # function of `environment` (+ `pool_name_suffix`) -- see that module's
  # `local.name` -- never of anything computed at apply time. Computing
  # the main pool's name here, identically, lets sandbox_runtime below
  # reference it directly instead of via `module.gpu_node_pool.
  # node_pool_name`, which would otherwise create a dependency cycle now
  # that module.gpu_node_pool (below) needs sandbox_runtime's rendered
  # bootstrap_script as its own `user_data` input (closing
  # infra/README.md known-gap #3). sandbox_runtime -> (this local only);
  # gpu_node_pool -> sandbox_runtime's output: a straight line, not a
  # cycle.
  gpu_node_pool_name = "${var.environment}-gpu-node-pool"

  # The node *label* value Karpenter stamps on nodes in the Ollama pool
  # (via that module's `labels` input below) -- kept as one local so the
  # NodePool's `labels` and model_serving_ollama's `node_pool_name`
  # nodeSelector are guaranteed to agree, rather than one module's output
  # (which names the NodePool *object*, not the node *label*) being
  # threaded into the other as if the two were the same string.
  ollama_node_pool_label = "ollama-gpu"

  # Same reasoning as ollama_node_pool_label above, for the main (vLLM)
  # GPU pool: gpu-node-pool/aws's `labels` variable defaults to
  # {"sdlc-auto.io/node-pool" = "gpu"}, which is the value actually
  # stamped on nodes and the value model_serving's nodeSelector must
  # match -- not module.gpu_node_pool.node_pool_name (the NodePool
  # *object's* name, e.g. "<environment>-gpu-node-pool"). Passing the
  # latter into model_serving.node_pool_name, as this file originally
  # did, produced a nodeSelector that could never match a real node: a
  # real bug (found during the Ollama-tier build, when writing this same
  # pattern for that tier surfaced the pre-existing main-pool version),
  # confirmed independently via gpu-node-pool/aws's own `labels` default
  # and model_serving's nodeSelector construction, fixed here rather than
  # left in place now that a real deployment is the actual goal.
  gpu_node_pool_label = "gpu"

  # Every node-role ARN Karpenter's controller must be allowed to
  # `iam:PassRole` to when launching an instance -- the main GPU pool's
  # role, plus the Ollama tier's own role when that tier is enabled. Used
  # by eks.tf's least-privilege Karpenter controller policy (replacing
  # the former PowerUserAccess placeholder).
  karpenter_node_role_arns = concat(
    [module.gpu_node_pool.node_role_arn],
    var.ollama_enabled ? [module.gpu_node_pool_ollama[0].node_role_arn] : []
  )

  # --- Ollama model-cache restore from S3 (see
  # modules/model-serving-ollama/README.md "Restoring the model cache
  # from S3") -- parses var.ollama_model_cache_s3_uri into the
  # bucket/prefix eks.tf's least-privilege IAM policy scopes itself to.
  # Off unless both ollama_enabled and ollama_model_cache_s3_uri are set;
  # this local is what actually gates the IAM role/policy in eks.tf and
  # the module.model_serving_ollama wiring below.
  ollama_model_cache_s3_enabled = var.ollama_enabled && var.ollama_model_cache_s3_uri != null && var.ollama_model_cache_s3_uri != ""

  # regex("^s3://([^/]+)/?(.*)$", ...) -> [bucket, prefix-without-leading-slash].
  # Guarded by the enabled flag above so an unset/null URI never reaches
  # regex() (which would error on a non-matching/empty string).
  ollama_model_cache_s3_parts   = local.ollama_model_cache_s3_enabled ? regex("^s3://([^/]+)/?(.*)$", var.ollama_model_cache_s3_uri) : null
  ollama_model_cache_bucket     = local.ollama_model_cache_s3_enabled ? local.ollama_model_cache_s3_parts[0] : null
  ollama_model_cache_prefix_raw = local.ollama_model_cache_s3_enabled ? local.ollama_model_cache_s3_parts[1] : null
  # Normalized to exactly one trailing slash (or "" for a bucket-root
  # cache with no prefix) so the IAM policy's `s3:prefix` condition and
  # object-ARN glob in eks.tf agree with each other and with what `aws
  # s3 sync` actually writes under the configured prefix.
  ollama_model_cache_prefix = local.ollama_model_cache_s3_enabled ? (
    local.ollama_model_cache_prefix_raw == "" ? "" : "${trimsuffix(local.ollama_model_cache_prefix_raw, "/")}/"
  ) : null
}

module "network" {
  source = "../../modules/network/aws"

  environment      = var.environment
  region           = var.region
  egress_allowlist = var.egress_allowlist
  tags             = var.tags
}

module "secrets" {
  source = "../../modules/secrets/aws-secrets-manager"

  environment  = var.environment
  secret_names = var.secret_names
  reader_principal_arns = [
    aws_iam_role.eks_system_nodes.arn,
  ]
  tags = var.tags
}

module "sandbox_runtime" {
  source = "../../modules/sandbox-runtime/firecracker"

  environment = var.environment
  # See locals.gpu_node_pool_name's comment above for why this is a
  # locally-computed name rather than module.gpu_node_pool.node_pool_name
  # (that reference would create a dependency cycle now that
  # module.gpu_node_pool consumes this module's bootstrap_script output).
  node_pool_name = local.gpu_node_pool_name
  tags           = var.tags
}

module "gpu_node_pool" {
  source = "../../modules/gpu-node-pool/aws"

  environment                = var.environment
  cluster_name               = aws_eks_cluster.this.name
  network_id                 = module.network.network_id
  gpu_subnet_ids             = module.network.gpu_subnet_ids
  egress_allowlist_group_id  = module.network.egress_allowlist_group_id
  instance_types             = var.instance_types
  capacity_type              = "spot"
  on_demand_fallback         = true
  max_interruption_frequency = var.max_interruption_frequency
  min_size                   = 0
  max_size                   = 1
  desired_size               = 1
  # Explicit rather than relying on the module's own default, matching
  # the Ollama tier's pattern below -- see locals.gpu_node_pool_label.
  labels = { "sdlc-auto.io/node-pool" = local.gpu_node_pool_label }
  # Closes infra/README.md known-gap #3: actually wires the firecracker
  # node-bootstrap script into the EC2NodeClass's userData, not just
  # leaving both modules independently declared.
  user_data = module.sandbox_runtime.bootstrap_script
  tags      = var.tags

  depends_on = [helm_release.karpenter]
}

# --- Ollama serving tier's own spot-only, scale-to-zero NodePool ----------
#
# A second instance of gpu-node-pool/aws (not a new module -- see that
# module's README "Composing this module more than once") with
# capacity_type = "spot" + on_demand_fallback = false (no on-demand
# fallback, unlike the shared pool above) and a short consolidate_after,
# so Karpenter actually reclaims the EC2 instance once KEDA scales the
# Ollama Deployment to 0 (see modules/model-serving-ollama's
# ScaledObject). No firecracker userData here: this tier only ever runs
# the ollama/ollama server image directly, no sandboxed agent
# tool-execution workload of its own.
module "gpu_node_pool_ollama" {
  count  = var.ollama_enabled ? 1 : 0
  source = "../../modules/gpu-node-pool/aws"

  environment               = var.environment
  pool_name_suffix          = "ollama"
  cluster_name              = aws_eks_cluster.this.name
  network_id                = module.network.network_id
  gpu_subnet_ids            = module.network.gpu_subnet_ids
  egress_allowlist_group_id = module.network.egress_allowlist_group_id
  instance_types            = [var.ollama_instance_type]
  capacity_type             = "spot"
  on_demand_fallback        = false
  consolidate_after         = var.ollama_consolidate_after
  labels                    = { "sdlc-auto.io/node-pool" = local.ollama_node_pool_label }
  min_size                  = 0
  max_size                  = 1
  desired_size              = 0
  tags                      = var.tags

  depends_on = [helm_release.karpenter]
}

module "spot_lifecycle" {
  source = "../../modules/spot-lifecycle"

  environment                  = var.environment
  cloud_provider               = "aws"
  cluster_name                 = aws_eks_cluster.this.name
  gpu_node_pool_name           = module.gpu_node_pool.node_pool_name
  checkpoint_webhook_url       = "http://orchestrator-${var.environment}.orchestrator.svc.cluster.local:8080/internal/checkpoint-webhook"
  observability_flush_endpoint = "http://loki.observability.svc.cluster.local:3100"
  warning_window_seconds       = 120
  tags                         = var.tags
}

module "model_serving" {
  source = "../../modules/model-serving"

  environment                      = var.environment
  model_artifact_uri               = var.model_artifact_uri
  model_weight_checksum            = var.model_weight_checksum
  tensor_parallel_size             = 1
  gpu_utilization_target           = 0.9
  replicas                         = 1
  node_pool_name                   = local.gpu_node_pool_label
  termination_grace_period_seconds = module.spot_lifecycle.warning_window_seconds - 30
  tags                             = var.tags
}

module "orchestrator" {
  source = "../../modules/orchestrator"

  environment                      = var.environment
  model_serving_endpoint           = module.model_serving.service_endpoint
  mesh_enabled                     = var.mesh_enabled
  mesh_provider                    = "linkerd"
  termination_grace_period_seconds = module.spot_lifecycle.warning_window_seconds - 20
  tags                             = var.tags
}

# --- Ollama serving tier (alongside vLLM, not a replacement) --------------
#
# Off by default (var.ollama_enabled = false); the vLLM path above
# (module.model_serving) is unaffected either way. See
# modules/model-serving-ollama/README.md for the pull-cost,
# context-length/VRAM, and KEDA/run-registry caveats this deployment
# carries.
module "model_serving_ollama" {
  count  = var.ollama_enabled ? 1 : 0
  source = "../../modules/model-serving-ollama"

  environment    = var.environment
  model_name     = var.ollama_model_name
  node_pool_name = local.ollama_node_pool_label
  instance_type  = var.ollama_instance_type
  # KEDA scale-to-zero is the mechanism, not the sole termination-grace
  # story -- see chart's own comment on why this tier has no warning-
  # window-sized preStop hook the way vLLM's does.
  termination_grace_period_seconds = 30
  keda_enabled                     = var.ollama_keda_enabled
  keda_metrics_api_url             = var.ollama_run_registry_metrics_url
  # Model-cache restore from S3 (see modules/model-serving-ollama/README.md
  # "Restoring the model cache from S3") -- both null unless
  # ollama_model_cache_s3_uri is actually set, in which case the IRSA role
  # eks.tf declares specifically for this (scoped to exactly this
  # bucket/prefix) is threaded in as service_account_role_arn.
  model_cache_s3_uri       = local.ollama_model_cache_s3_enabled ? var.ollama_model_cache_s3_uri : null
  service_account_role_arn = local.ollama_model_cache_s3_enabled ? aws_iam_role.ollama_model_cache[0].arn : null
  tags                     = var.tags

  # helm_release.keda: this module's own ScaledObject (kubernetes_manifest)
  # is a CRD instance -- it applies fine against the API server whether or
  # not KEDA's controller is actually running to reconcile it, but with no
  # controller watching it would just sit there inert, silently not scaling
  # anything. Referencing the whole (possibly count = 0, when
  # ollama_keda_enabled = false) resource here means: when it *is* enabled,
  # the controller is guaranteed to exist before this module's ScaledObject
  # is applied.
  depends_on = [module.gpu_node_pool_ollama, helm_release.keda]
}

module "observability" {
  source = "../../modules/observability"

  environment                        = var.environment
  storage_class                      = var.storage_class
  grafana_admin_password_secret_name = "grafana-admin-password"
  # Closes infra/README.md known-gap #6: an ExternalSecret now actually
  # syncs the AWS Secrets Manager entry into the Kubernetes Secret
  # Grafana's chart references by name (see modules/observability/README.md
  # for exactly what "sync" means here given seed-secrets.sh's real
  # single-opaque-string-per-secret shape).
  external_secrets_enabled     = true
  aws_region                   = var.region
  eso_service_account_role_arn = aws_iam_role.external_secrets_grafana.arn
  tags                         = var.tags

  # helm_release.external_secrets: same reasoning as model_serving_ollama's
  # depends_on on helm_release.keda above -- this module's own
  # SecretStore/ExternalSecret (kubernetes_manifest) resources need the ESO
  # controller (and the CRDs its chart installs) to exist first, or they'd
  # apply successfully but sit unreconciled.
  depends_on = [helm_release.external_secrets]
}
