# model-serving-ollama — deploys the local Helm chart (./chart: Ollama +
# the pinned model, spot-only/scale-to-zero tier). Cloud-agnostic: this
# module contains no aws_*/google_*/azurerm_* resources, only
# kubernetes/helm providers, same reasoning as modules/model-serving.
#
# --- Why a sibling module, not a `serving_backend` switch on
# modules/model-serving (this deliverable's brief explicitly asks for a
# justified choice here) ---
#
# modules/model-serving's chart is built specifically around vLLM's own
# CLI surface (`--model`, `--tensor-parallel-size`,
# `--gpu-memory-utilization`, `--served-model-name`), its own health path
# (`/health`), and a vLLM-specific best-effort `preStop` drain hook tied
# to spot-lifecycle's warning-window budget (see that chart's own
# comment). Ollama differs on every one of those axes: no tensor-
# parallel/gpu-utilization flags (single-GPU only for this tier), no
# `/health` endpoint (root `/` is the closest equivalent), a materially
# different startup story (a `postStart` `ollama pull` step vLLM has no
# equivalent of, and which dominates this tier's cold-start behavior),
# and a different default port/API shape (11434, OpenAI-compatible
# routes under `/v1/*`, vs vLLM's 8000). This tier is also the one that
# is spot-only and KEDA-scaled-to-zero (see below) -- vLLM's tier is not.
# Threading all of that through one chart via `serving_backend ==
# "ollama"` conditionals would make the chart harder to read for both
# backends without making either simpler, and would make the *contract*
# of the two `helm_release`s (image, ports, health checks, autoscaling)
# look unified when it genuinely is not. A sibling module with the same
# variable-naming conventions (environment/namespace/node_pool_name/
# tenant_id/tags) keeps each backend's logic isolated while remaining
# trivially easy to compose side by side in an environment (see
# environments/pilot-aws-g7e/main.tf) -- exactly the "real pilot that
# wants to compare" case this deliverable's brief names. This mirrors how
# gpu-node-pool/{aws,gcp,azure,baremetal} are already siblings sharing
# one variable/output contract rather than one parameterized module with
# a `cloud` switch, rather than inventing a new pattern for this repo.

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
  # Same fix as modules/model-serving/main.tf's identical local -- see
  # that file's comment (the k8s-label-vs-cloud-tag mismatch a real
  # `tofu plan` caught during this repo's original build).
  k8s_safe_tags = { for k, v in var.tags : replace(k, ":", ".") => v }
}

resource "kubernetes_namespace_v1" "this" {
  metadata {
    name   = var.namespace
    labels = merge(local.k8s_safe_tags, { "sdlc-auto.io/component" = "model-serving-ollama" })
  }
}

resource "helm_release" "ollama" {
  name      = "ollama-${var.environment}"
  chart     = "${path.module}/chart"
  namespace = kubernetes_namespace_v1.this.metadata[0].name

  values = [
    yamlencode({
      environment = var.environment
      tenantId    = coalesce(var.tenant_id, "shared")
      image = {
        repository = "ollama/ollama"
        tag        = var.image_tag
      }
      model = {
        name = var.model_name
      }
      # When KEDA is enabled, the Deployment's *base* (Helm-managed)
      # replica count must be 0, not var.replicas -- KEDA's ScaledObject
      # below (minReplicaCount = 0) is what's supposed to scale 0->1 in
      # response to real demand, but it only takes over scaling *after*
      # Helm creates/updates the Deployment. Passing var.replicas (a
      # default of 1) here unconditionally meant every `tofu apply`/
      # `helm upgrade` immediately scheduled a pod -- and, since a
      # scheduled pod is exactly what makes Karpenter launch a real spot
      # GPU instance, immediately spun one up regardless of whether any
      # real work had actually triggered it, with no scale-back-down path
      # at all whenever keda_enabled = false (there is no ScaledObject in
      # that case, so nothing would ever bring replicas back to 0). Real
      # bug, found and fixed before Phase 2's first real apply -- var.
      # replicas now only matters for the (non-default) case of running
      # this tier without KEDA at all, where a static replica count is
      # the only option.
      replicas = var.keda_enabled ? 0 : var.replicas
      nodeSelector = merge(
        { "sdlc-auto.io/node-pool" = var.node_pool_name },
        var.instance_type != null ? { "node.kubernetes.io/instance-type" = var.instance_type } : {}
      )
      tolerations = [{
        key      = "sdlc-auto.io/gpu"
        operator = "Equal"
        value    = "true"
        effect   = "NoSchedule"
      }]
      terminationGracePeriodSeconds = var.termination_grace_period_seconds
      resources = {
        limits = {
          "nvidia.com/gpu" = var.gpu_count
          cpu              = var.cpu_limit
          memory           = var.memory_limit
        }
        requests = {
          "nvidia.com/gpu" = var.gpu_count
          cpu              = var.cpu_request
          memory           = var.memory_request
        }
      }
      # Model-cache restore from S3 -- both default to "" (chart-side
      # truthiness check, see templates/serviceaccount.yaml and
      # templates/deployment.yaml) so leaving both module variables unset
      # renders neither the ServiceAccount nor the restore init container,
      # i.e. exactly this chart's pre-existing pull-only behavior.
      serviceAccount = {
        roleArn = coalesce(var.service_account_role_arn, "")
      }
      modelCache = {
        s3Uri = coalesce(var.model_cache_s3_uri, "")
      }
    })
  ]

  lifecycle {
    precondition {
      # The restore init container's `aws s3 sync` call has no real AWS
      # credentials without an IRSA-annotated ServiceAccount -- catch this
      # misconfiguration at plan/apply time rather than as a silent
      # AccessDenied loop inside the init container at pod-start time.
      condition     = var.model_cache_s3_uri == null || var.service_account_role_arn != null
      error_message = "service_account_role_arn must be set when model_cache_s3_uri is set -- the model-cache-restore init container has no AWS credentials otherwise. See README.md \"Restoring the model cache from S3\"."
    }
  }
}

# --- KEDA ScaledObject: spot-only, scale-to-zero autoscaling ---------------
#
# Placed here, next to the Deployment it targets, rather than inside
# modules/gpu-node-pool/aws -- even though that module is where this
# repo's other CRD-via-`kubernetes_manifest` precedent already lives (its
# Karpenter EC2NodeClass/NodePool). A ScaledObject's `scaleTargetRef`
# names a Deployment; that Deployment is a Helm-managed resource owned by
# this module (gpu-node-pool/aws knows nothing about Helm releases or
# namespaces). Same resource type/pattern as that precedent
# (`kubernetes_manifest` wrapping a controller-defined CRD this module
# assumes is already installed), applied where the thing it targets
# actually lives -- see README for the full reasoning and the KEDA
# controller / run-registry-endpoint prerequisites this assumes.
resource "kubernetes_manifest" "ollama_scaled_object" {
  count = var.keda_enabled ? 1 : 0

  manifest = {
    apiVersion = "keda.sh/v1alpha1"
    kind       = "ScaledObject"
    metadata = {
      name      = "ollama-${var.environment}"
      namespace = kubernetes_namespace_v1.this.metadata[0].name
    }
    spec = {
      scaleTargetRef = {
        name = helm_release.ollama.name
      }
      # Single-cell, no GPU sharing (per the platform's design): this
      # tier is either fully up (1 pod, scheduled onto a spot node by
      # gpu-node-pool/aws's spot-only NodePool instance) or fully down
      # (0 pods -- and once nothing schedules against that NodePool,
      # its own `disruption.consolidationPolicy =
      # WhenEmptyOrUnderutilized` + short `consolidateAfter` is what
      # actually reclaims the now-idle EC2 instance; see that module's
      # README).
      minReplicaCount = 0
      maxReplicaCount = 1
      cooldownPeriod  = var.keda_cooldown_period_seconds
      pollingInterval = var.keda_polling_interval_seconds
      triggers = [
        {
          type = "metrics-api"
          metadata = {
            # See README "KEDA trigger: run-registry integration gap" for
            # the exact endpoint/response contract this expects, and
            # this deliverable's explicit finding that run-registry does
            # not expose it today.
            targetValue   = "1"
            url           = var.keda_metrics_api_url
            valueLocation = "count"
            method        = "GET"
          }
        }
      ]
    }
  }

  lifecycle {
    precondition {
      condition     = var.keda_metrics_api_url != ""
      error_message = "keda_metrics_api_url must be set when keda_enabled = true -- see README's \"KEDA trigger: run-registry integration gap\" section for the expected endpoint contract."
    }
  }
}
