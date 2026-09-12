# model-serving — deploys the local Helm chart (./chart: vLLM + pinned
# model). Cloud-agnostic: this module contains no aws_*/google_*/azurerm_*
# resources, only kubernetes/helm providers, so it needs no per-cloud
# variant (spec §14.3 lists it flat, not under {aws,gcp,azure,baremetal}).

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    helm = {
      source  = "hashicorp/helm"
      version = ">= 2.13"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.31"
    }
  }
}

locals {
  # Kubernetes label keys allow at most one '/' (prefix delimiter) and no
  # ':' at all, but var.tags carries cloud-resource-tag-style keys like
  # "sdlc-auto:managed-by" (valid as e.g. an AWS tag, invalid as a k8s
  # label — surfaced by D6/services/tenant-cell's real `tofu plan` run
  # against this module, see infra/README.md). Sanitize ':' -> '.' before
  # use as labels; values and the tags' own use elsewhere are unaffected.
  k8s_safe_tags = { for k, v in var.tags : replace(k, ":", ".") => v }
}

resource "kubernetes_namespace_v1" "this" {
  metadata {
    name   = var.namespace
    labels = merge(local.k8s_safe_tags, { "sdlc-auto.io/component" = "model-serving" })
  }
}

resource "helm_release" "vllm" {
  name      = "vllm-${var.environment}"
  chart     = "${path.module}/chart"
  namespace = kubernetes_namespace_v1.this.metadata[0].name

  values = [
    yamlencode({
      environment = var.environment
      tenantId    = coalesce(var.tenant_id, "shared")
      image = {
        # Pinned at the environment composition layer via a values
        # override in real use; left generic here since the exact vLLM
        # image tag is an operational choice, not an infra-module concern.
        repository = "vllm/vllm-openai"
        tag        = "latest" # environments/* SHOULD override this to a pinned digest
      }
      model = {
        artifactUri    = var.model_artifact_uri
        weightChecksum = var.model_weight_checksum
        tensorParallel = var.tensor_parallel_size
        gpuUtilization = var.gpu_utilization_target
      }
      replicas = var.replicas
      nodeSelector = {
        "sdlc-auto.io/node-pool" = var.node_pool_name
      }
      tolerations = [{
        key      = "sdlc-auto.io/gpu"
        operator = "Equal"
        value    = "true"
        effect   = "NoSchedule"
      }]
      terminationGracePeriodSeconds = var.termination_grace_period_seconds
    })
  ]
}
