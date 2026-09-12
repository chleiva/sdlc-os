# prod-gcp — Phase 1.5 portability-proof STRUCTURE (see README.md for what
# this does and does not prove yet). Compare against
# ../pilot-aws-g7e/main.tf or ../prod-aws/main.tf: the only `source` lines
# that differ are network and gpu-node-pool (and secrets, for the same
# reason). model-serving, orchestrator, observability, and
# sandbox-runtime/firecracker are referenced identically — this is the
# module-contract rule (spec §14.3, §14.10) made structurally visible.

module "network" {
  source = "../../modules/network/gcp" # <- only line that differs from prod-aws (network/aws)

  environment      = var.environment
  region           = var.region
  egress_allowlist = var.egress_allowlist
  tags             = var.tags
}

module "secrets" {
  source = "../../modules/secrets/gcp-secret-manager" # <- differs from prod-aws (secrets/aws-secrets-manager)

  environment  = var.environment
  secret_names = var.secret_names
  tags         = var.tags
}

module "gpu_node_pool" {
  source = "../../modules/gpu-node-pool/gcp" # <- only line that differs from prod-aws (gpu-node-pool/aws)

  environment               = var.environment
  cluster_name              = "${var.environment}-cluster" # placeholder: no real GKE cluster resource yet, see README.md
  network_id                = module.network.network_id
  gpu_subnet_ids            = module.network.gpu_subnet_ids
  egress_allowlist_group_id = module.network.egress_allowlist_group_id
  instance_types            = var.instance_types
  capacity_type             = "spot"
  min_size                  = 0
  max_size                  = 1
  desired_size              = 1
  tags                      = var.tags
}

# --- Everything below is BYTE-FOR-BYTE IDENTICAL in shape to
# prod-aws/main.tf's equivalent blocks — same module source, same
# variable wiring. This is deliberate: it's the part of the portability
# proof that's supposed to require zero changes. ---------------------------

module "sandbox_runtime" {
  source = "../../modules/sandbox-runtime/firecracker"

  environment    = var.environment
  node_pool_name = module.gpu_node_pool.node_pool_name
  tags           = var.tags
}

module "model_serving" {
  source = "../../modules/model-serving"

  environment            = var.environment
  model_artifact_uri     = var.model_artifact_uri
  model_weight_checksum  = var.model_weight_checksum
  tensor_parallel_size   = 1
  gpu_utilization_target = 0.9
  replicas               = 1
  node_pool_name         = module.gpu_node_pool.node_pool_name
  tags                   = var.tags
}

module "orchestrator" {
  source = "../../modules/orchestrator"

  environment            = var.environment
  model_serving_endpoint = module.model_serving.service_endpoint
  mesh_enabled           = var.mesh_enabled
  mesh_provider          = "linkerd"
  tags                   = var.tags
}

module "observability" {
  source = "../../modules/observability"

  environment                        = var.environment
  storage_class                      = var.storage_class
  grafana_admin_password_secret_name = "grafana-admin-password"
  tags                               = var.tags
}
