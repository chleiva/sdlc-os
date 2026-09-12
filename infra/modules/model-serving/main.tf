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

resource "kubernetes_namespace_v1" "this" {
  metadata {
    name   = var.namespace
    labels = merge(var.tags, { "sdlc-auto.io/component" = "model-serving" })
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
