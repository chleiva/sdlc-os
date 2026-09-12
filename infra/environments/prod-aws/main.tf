# prod-aws — production composition, same module set as pilot-aws-g7e,
# sized per the scaling ladder (spec §13.3, §14.9). See README.md for
# what differs from the pilot and this environment's apply-tested status.
#
# This file composes the module tree; it creates no resources of its own
# beyond wiring module inputs/outputs together. See eks.tf for the EKS
# control plane + system node group these modules attach to.

module "network" {
  source = "../../modules/network/aws"

  environment      = var.environment
  region           = var.region
  az_count         = var.az_count
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
  min_size                   = var.node_pool_min_size
  max_size                   = var.node_pool_max_size
  desired_size               = var.node_pool_desired_size
  tags                       = var.tags

  depends_on = [helm_release.karpenter]
}

module "sandbox_runtime" {
  source = "../../modules/sandbox-runtime/firecracker"

  environment    = var.environment
  node_pool_name = module.gpu_node_pool.node_pool_name
  tags           = var.tags
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
  node_pool_name                   = module.gpu_node_pool.node_pool_name
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

module "observability" {
  source = "../../modules/observability"

  environment                        = var.environment
  storage_class                      = var.storage_class
  grafana_admin_password_secret_name = "grafana-admin-password"
  tags                               = var.tags
}
