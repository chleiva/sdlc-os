# two-tenants — a validation/proof composition, NOT a real deployable
# environment. It exists to run `tofu init`/`validate`/`fmt`/`plan`
# against two concurrent instantiations of ../../modules/tenant-cell and
# show, for real, that they produce structurally distinct resource sets
# (distinct node pool names, distinct IAM role names/instance profiles,
# distinct model-serving namespaces/service endpoints) -- the "no two
# tenants ever share a node pool" acceptance criterion, inspectable
# directly in `tofu plan`'s output. See README.md for the exact commands
# and what they actually produce in this environment (no live cloud
# account or Kubernetes cluster available).
#
# A real environments/* composition (following environments/pilot-aws-g7e's
# own layout) would supply real `cluster_name`/`network_id`/subnet ids
# from its own aws_eks_cluster + network module, and real per-tenant
# model artifact URIs/checksums -- this composition stands those in with
# plain variables so it can be validated standalone, without depending on
# a real EKS cluster resource existing anywhere.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.40"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.31"
    }
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.13"
    }
  }
}

module "tenant_acme" {
  source = "../../modules/tenant-cell"

  base_environment          = var.base_environment
  tenant_id                 = "acme"
  cluster_name              = var.cluster_name
  network_id                = var.network_id
  gpu_subnet_ids            = var.gpu_subnet_ids
  egress_allowlist_group_id = var.egress_allowlist_group_id

  instance_types = ["g7e.2xlarge"]

  primary_model_artifact_uri    = "s3://sdlc-auto-model-artifacts/ornith-1.5-35b-a3b/nvfp4/${var.primary_model_weight_checksum}/"
  primary_model_weight_checksum = var.primary_model_weight_checksum

  # Phase-0-tier tenant: no second self-hosted model -- relies on a
  # frontier-API escalation path (validated by the Python control-plane,
  # not this IaC layer).
  reviewer_model_artifact_uri = null

  tags = var.tags
}

module "tenant_globex" {
  source = "../../modules/tenant-cell"

  base_environment          = var.base_environment
  tenant_id                 = "globex"
  cluster_name              = var.cluster_name
  network_id                = var.network_id
  gpu_subnet_ids            = var.gpu_subnet_ids
  egress_allowlist_group_id = var.egress_allowlist_group_id

  instance_types = ["g7e.12xlarge"]

  primary_model_artifact_uri    = "s3://sdlc-auto-model-artifacts/ornith-1.5-35b-a3b/bf16/${var.primary_model_weight_checksum}/"
  primary_model_weight_checksum = var.primary_model_weight_checksum

  # Larger-tier tenant: room for a second, architecturally-distinct
  # self-hosted reviewer model on the same dedicated hardware (§13.4/§13.7).
  reviewer_model_artifact_uri    = "s3://sdlc-auto-model-artifacts/llama4-reviewer/bf16/${var.reviewer_model_weight_checksum}/"
  reviewer_model_weight_checksum = var.reviewer_model_weight_checksum
  reviewer_tensor_parallel_size  = 1

  tags = var.tags
}
