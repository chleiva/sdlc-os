# observability — kube-prometheus-stack (Prometheus + Grafana), Loki, and
# Tempo, all via their public Helm charts. Cloud-agnostic: only the
# storage_class variable differs by cloud, and that's supplied by the
# environment composition, not hardcoded here.

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
    labels = merge(var.tags, { "sdlc-auto.io/component" = "observability" })
  }
}

locals {
  grafana_admin_password_remote_key = coalesce(
    var.grafana_admin_password_remote_key,
    "${var.environment}/${var.grafana_admin_password_secret_name}"
  )
}

resource "helm_release" "kube_prometheus_stack" {
  name       = "kube-prometheus-stack"
  repository = "https://prometheus-community.github.io/helm-charts"
  chart      = "kube-prometheus-stack"
  namespace  = kubernetes_namespace_v1.this.metadata[0].name

  values = [
    yamlencode({
      prometheus = {
        prometheusSpec = {
          retention = var.metrics_retention
          storageSpec = {
            volumeClaimTemplate = {
              spec = {
                storageClassName = var.storage_class
                resources        = { requests = { storage = "50Gi" } }
              }
            }
          }
        }
      }
      grafana = {
        # Admin password is injected from the secret store at runtime via
        # this existingSecret reference — never set inline (spec §14.6).
        admin = {
          existingSecret = var.grafana_admin_password_secret_name
          userKey        = "admin-user"
          passwordKey    = "admin-password"
        }
      }
    })
  ]
}

resource "helm_release" "loki" {
  name       = "loki"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "loki-stack"
  namespace  = kubernetes_namespace_v1.this.metadata[0].name

  values = [
    yamlencode({
      loki = {
        persistence = {
          enabled          = true
          storageClassName = var.storage_class
        }
      }
      # Ships an alongside promtail DaemonSet, which is what receives the
      # spot-lifecycle watcher's "flush buffered logs off-node" signal
      # (spec §14.8 step 4) in practice — it's already tailing node logs
      # continuously rather than buffering on the GPU node itself.
      promtail = { enabled = true }
    })
  ]
}

resource "helm_release" "tempo" {
  name       = "tempo"
  repository = "https://grafana.github.io/helm-charts"
  chart      = "tempo"
  namespace  = kubernetes_namespace_v1.this.metadata[0].name

  values = [
    yamlencode({
      persistence = {
        enabled          = true
        storageClassName = var.storage_class
      }
    })
  ]
}

# --- External Secrets Operator: SecretStore + ExternalSecret CRD
# instances syncing the Grafana admin password from AWS Secrets Manager
# into the Kubernetes Secret `grafana.admin.existingSecret` above
# references (closes infra/README.md known-gap #6). Assumes ESO's
# controller (CRDs + controller Deployment) is already installed
# cluster-wide via its own Helm chart -- this module only declares the
# CRD instances that tell an already-running ESO controller what to
# sync, the same already-running-controller assumption
# gpu-node-pool/aws makes about Karpenter (see that module's header
# comment) and this deliverable's own KEDA assumption
# (modules/model-serving-ollama/README.md).

resource "kubernetes_service_account_v1" "eso_grafana" {
  count = var.external_secrets_enabled ? 1 : 0

  metadata {
    name      = var.eso_service_account_name
    namespace = kubernetes_namespace_v1.this.metadata[0].name
    annotations = var.eso_service_account_role_arn != null ? {
      "eks.amazonaws.com/role-arn" = var.eso_service_account_role_arn
    } : {}
  }
}

resource "kubernetes_manifest" "eso_secret_store" {
  count = var.external_secrets_enabled ? 1 : 0

  manifest = {
    apiVersion = "external-secrets.io/v1beta1"
    kind       = "SecretStore"
    metadata = {
      name      = var.secret_store_name
      namespace = kubernetes_namespace_v1.this.metadata[0].name
    }
    spec = {
      provider = {
        aws = {
          service = "SecretsManager"
          region  = var.aws_region
          auth = {
            jwt = {
              serviceAccountRef = {
                name = kubernetes_service_account_v1.eso_grafana[0].metadata[0].name
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = var.aws_region != null
      error_message = "aws_region must be set when external_secrets_enabled = true."
    }
  }

  depends_on = [kubernetes_service_account_v1.eso_grafana]
}

resource "kubernetes_manifest" "grafana_admin_password_external_secret" {
  count = var.external_secrets_enabled ? 1 : 0

  manifest = {
    apiVersion = "external-secrets.io/v1beta1"
    kind       = "ExternalSecret"
    metadata = {
      name      = "${var.grafana_admin_password_secret_name}-sync"
      namespace = kubernetes_namespace_v1.this.metadata[0].name
    }
    spec = {
      refreshInterval = var.external_secret_refresh_interval
      secretStoreRef = {
        name = var.secret_store_name
        kind = "SecretStore"
      }
      target = {
        # Name matches `grafana.admin.existingSecret` above exactly --
        # this is the Kubernetes Secret the kube-prometheus-stack
        # release already expects to find.
        name           = var.grafana_admin_password_secret_name
        creationPolicy = "Owner"
        # See README "Why admin-user is not synced from Secrets
        # Manager": seed-secrets.sh stores one opaque string per secret
        # name, not a JSON object with separate properties, so the
        # username is templated as a static, non-secret value here
        # rather than invented as a fake JSON-property read.
        template = {
          data = {
            "admin-user" = var.grafana_admin_username
          }
        }
      }
      data = [
        {
          secretKey = "admin-password"
          remoteRef = {
            key = local.grafana_admin_password_remote_key
          }
        },
      ]
    }
  }

  depends_on = [kubernetes_manifest.eso_secret_store]
}
