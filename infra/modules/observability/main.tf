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
