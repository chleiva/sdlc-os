terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
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

# TODO, honestly unresolved: no google_container_cluster (GKE) resource
# exists yet in this composition (see README.md) — network/gcp and
# gpu-node-pool/gcp are Phase 1.5 stubs, and writing the cluster resource
# ahead of them would mean guessing at their eventual outputs. The
# kubernetes/helm provider blocks below are therefore placeholders wired
# to variables rather than to a real cluster's computed attributes (as
# pilot-aws-g7e/providers.tf does via `aws_eks_cluster.this`), only so
# `tofu validate` can check the module composition's shape.

variable "cluster_endpoint_placeholder" {
  type        = string
  description = "PLACEHOLDER until a real google_container_cluster resource exists in this environment. Do not treat this as a real provider config path."
  default     = "https://not-yet-provisioned.invalid"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "kubernetes" {
  host  = var.cluster_endpoint_placeholder
  token = "" # populated from a real cluster auth data source once one exists
}

provider "helm" {
  kubernetes = {
    host  = var.cluster_endpoint_placeholder
    token = ""
  }
}
