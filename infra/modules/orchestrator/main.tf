# orchestrator — namespace, RBAC, NetworkPolicy (mTLS-adjacent isolation),
# and the Helm skeleton the actual agent-runtime container (D2) deploys
# into. Cloud-agnostic (kubernetes/helm providers only).

terraform {
  required_version = ">= 1.6.0"

  required_providers {
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

resource "kubernetes_namespace_v1" "this" {
  metadata {
    name   = var.namespace
    labels = merge(var.tags, { "sdlc-auto.io/component" = "orchestrator" })
  }
}

resource "kubernetes_service_account_v1" "orchestrator" {
  metadata {
    name      = "orchestrator"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
}

# Zero-trust default: orchestrator namespace denies all ingress/egress
# except what's explicitly allowed below (spec §7.5, §14.5). This is the
# infra-level half of the mesh's mTLS requirement — the NetworkPolicy
# constrains *which* pods may talk to which; the mesh (when mesh_enabled)
# constrains *how* (mTLS) once they do.
resource "kubernetes_network_policy_v1" "default_deny" {
  metadata {
    name      = "default-deny-all"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  spec {
    pod_selector {}
    policy_types = ["Ingress", "Egress"]
  }
}

resource "kubernetes_network_policy_v1" "allow_model_serving_egress" {
  metadata {
    name      = "allow-model-serving-egress"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  spec {
    pod_selector {
      match_labels = {
        "app.kubernetes.io/name" = "orchestrator"
      }
    }
    policy_types = ["Egress"]
    egress {
      to {
        namespace_selector {} # same-cluster; refined to model-serving's namespace label once that module's namespace label is stable
      }
      # DNS is required for in-cluster service resolution of
      # model_serving_endpoint; scoping to specific ports here rather than
      # opening egress to "any" destination.
      ports {
        port     = 8000
        protocol = "TCP"
      }
      ports {
        port     = 53
        protocol = "UDP"
      }
    }
  }
}

resource "kubernetes_config_map_v1" "orchestrator_config" {
  metadata {
    name      = "orchestrator-config"
    namespace = kubernetes_namespace_v1.this.metadata[0].name
  }
  data = {
    MODEL_SERVING_ENDPOINT    = var.model_serving_endpoint
    CHECKPOINT_STORE_ENDPOINT = var.checkpoint_store_endpoint
    TENANT_ID                 = coalesce(var.tenant_id, "shared")
    ENVIRONMENT               = var.environment
  }
}

# --- Service mesh (mTLS) between orchestrator, MCP servers, model-serving --

resource "helm_release" "linkerd_crds" {
  count            = var.mesh_enabled && var.mesh_provider == "linkerd" ? 1 : 0
  name             = "linkerd-crds"
  repository       = "https://helm.linkerd.io/stable"
  chart            = "linkerd-crds"
  namespace        = "linkerd"
  create_namespace = true
}

resource "helm_release" "linkerd_control_plane" {
  count      = var.mesh_enabled && var.mesh_provider == "linkerd" ? 1 : 0
  name       = "linkerd-control-plane"
  repository = "https://helm.linkerd.io/stable"
  chart      = "linkerd-control-plane"
  namespace  = "linkerd"

  depends_on = [helm_release.linkerd_crds]
}

# --- Orchestrator + MCP server skeleton Helm chart --------------------------
#
# D2 (agent orchestrator core) and F3 (MCP tool contracts) replace this
# chart's placeholder image with the real build; F1's job stops at "a
# real, deployable skeleton exists for bootstrap.sh's smoke run to probe."

resource "helm_release" "orchestrator" {
  name      = "orchestrator-${var.environment}"
  chart     = "${path.module}/chart"
  namespace = kubernetes_namespace_v1.this.metadata[0].name

  values = [
    yamlencode({
      environment                   = var.environment
      tenantId                      = coalesce(var.tenant_id, "shared")
      serviceAccountName            = kubernetes_service_account_v1.orchestrator.metadata[0].name
      modelServingEndpoint          = var.model_serving_endpoint
      terminationGracePeriodSeconds = var.termination_grace_period_seconds
      meshInjectionEnabled          = var.mesh_enabled
    })
  ]

  depends_on = [
    kubernetes_config_map_v1.orchestrator_config,
    kubernetes_network_policy_v1.allow_model_serving_egress,
  ]
}
