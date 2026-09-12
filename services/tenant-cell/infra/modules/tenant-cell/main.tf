# tenant-cell — calls F1's gpu-node-pool/aws and model-serving modules,
# parameterized per tenant (spec §14.13, §13.7). This module writes no
# aws_*/kubernetes_*/helm_* resources of its own beyond what those two
# calls produce -- it is a composition layer, per this deliverable's
# brief ("D6 parameterizes and calls F1's gpu-node-pool and model-serving
# modules per tenant; it doesn't write the base Terraform/OpenTofu").
#
# Only the AWS gpu-node-pool variant is wired here (matching F1's own
# pilot-aws-g7e reference environment and this environment's real-build
# scope); the {gcp,azure,baremetal} variants share the same variables.tf
# contract (see infra/modules/gpu-node-pool/*/variables.tf) so swapping
# `source` below to one of those, per environment, needs no other change
# in this file.

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

locals {
  # tenant_id is sanitized into a DNS-1123-safe slug (lowercased,
  # non [a-z0-9] runs collapsed to '-', leading/trailing '-' trimmed,
  # "tenant" fallback if that leaves nothing) and disambiguated with an
  # 8-hex-char sha256 suffix of the *raw* tenant_id, so two different raw
  # tenant ids can never collapse onto the same slug purely because
  # sanitization discarded what distinguished them (e.g. "Acme Corp!" vs
  # "acme_corp" both sanitize to "acme-corp" before the suffix disambiguates
  # them). This is the literal same formula as
  # services/tenant-cell/src/tenant_cell/naming.py's `tenant_slug()` --
  # kept in lockstep by hand; `tests/test_naming.py` pins the exact HCL
  # expressions below so a drift is caught as a test failure, not silently.
  tenant_slug_sanitized = trim(replace(lower(var.tenant_id), "/[^a-z0-9]+/", "-"), "-")
  tenant_slug           = "${local.tenant_slug_sanitized != "" ? local.tenant_slug_sanitized : "tenant"}-${substr(sha256(var.tenant_id), 0, 8)}"

  # The per-tenant environment name every resource this module creates
  # (directly or via the modules it calls) derives from. Matches
  # services/tenant-cell/src/tenant_cell/naming.py's
  # `tenant_environment()` formula exactly -- see that module's
  # docstring. Kept in one local so it's computed once, not re-typed at
  # every call site below.
  tenant_environment = "${var.base_environment}-tenant-${local.tenant_slug}"

  tags = merge(var.tags, {
    "sdlc-auto:tenant-id"        = var.tenant_id
    "sdlc-auto:module"           = "tenant-cell"
    "sdlc-auto:base-environment" = var.base_environment
  })

  # gpu-node-pool's own `labels` variable defaults to a generic, non-
  # tenant-scoped value ({"sdlc-auto.io/node-pool" = "gpu"}) shared by
  # every caller that doesn't override it -- fine for F1's single-tenant
  # pilot composition (only one pool exists), but NOT fine here: if two
  # tenants' node pools both stamped that same generic label onto their
  # nodes, model-serving's nodeSelector (which matches on
  # "sdlc-auto.io/node-pool" = <node_pool_name>) would still resolve
  # correctly per tenant ONLY if the label's *value* is this tenant's own
  # unique pool name, not the shared generic string. This module computes
  # that value itself (replicating gpu-node-pool's own
  # `"${var.environment}-gpu-node-pool"` formula, since Terraform cannot
  # reference a module's own output as an input to that same module call)
  # and passes it explicitly, so the label a node actually carries always
  # matches the nodeSelector value model-serving is given below.
  #
  # FLAGGED FOR HUMAN REVIEW: this is this deliverable's own fix for a
  # gap in F1's reference composition (environments/pilot-aws-g7e/main.tf
  # never overrides `labels`, which is harmless there only because a
  # single-tenant environment has just one pool) -- confirm this
  # replicated-formula approach is acceptable versus, alternatively,
  # exposing a `node_pool_name` output-echo variable on gpu-node-pool
  # itself so callers never have to restate the naming formula. That
  # would be an F1 module contract change, out of this deliverable's
  # scope to make unilaterally (per CLAUDE.md's shared-contract rule).
  computed_node_pool_name = "${local.tenant_environment}-gpu-node-pool"

  node_pool_labels = {
    "sdlc-auto.io/node-pool" = local.computed_node_pool_name
    "sdlc-auto.io/tenant-id" = local.tenant_slug
  }

  # model-serving's `tags` variable is applied directly as a Kubernetes
  # Namespace's `metadata.labels` (infra/modules/model-serving/main.tf's
  # `kubernetes_namespace_v1.this`), NOT as cloud-provider resource tags
  # the way gpu-node-pool's `tags` is -- Kubernetes label keys/values
  # forbid ':' in the name part (only alphanumeric, '-', '_', '.' are
  # legal), so `local.tags`' colon-separated convention
  # ("sdlc-auto:tenant-id") cannot be reused here as-is; the Kubernetes
  # provider rejects it (confirmed via `tofu plan` on this deliverable's
  # own compositions/two-tenants -- see that composition's README.md).
  #
  # FLAGGED FOR HUMAN REVIEW: this is a latent, pre-existing gap in F1's
  # reference environment, not something this deliverable introduces --
  # environments/pilot-aws-g7e/variables.tf's own default `var.tags`
  # (`"sdlc-auto:managed-by"`, `"sdlc-auto:phase"`) is passed straight
  # into `module.model_serving`'s `tags` there too, and would fail the
  # same Kubernetes label validation the moment that composition's own
  # `tofu plan` reached a real, reachable cluster (it never has, under
  # this same hard-constraint environment) -- it just hasn't been
  # exercised for real yet. This module works around it at its own call
  # sites (dot-separated keys below) rather than fixing model-serving
  # itself, which is out of this deliverable's scope.
  model_serving_labels = merge(
    { for k, v in var.tags : replace(k, ":", ".") => v },
    {
      "sdlc-auto.io/tenant-id" = local.tenant_slug
      "sdlc-auto.io/module"    = "tenant-cell"
    }
  )
}

module "gpu_node_pool" {
  source = "../../../../../infra/modules/gpu-node-pool/aws"

  environment                = local.tenant_environment
  cluster_name               = var.cluster_name
  network_id                 = var.network_id
  gpu_subnet_ids             = var.gpu_subnet_ids
  egress_allowlist_group_id  = var.egress_allowlist_group_id
  instance_types             = var.instance_types
  capacity_type              = var.capacity_type
  on_demand_fallback         = var.on_demand_fallback
  max_interruption_frequency = var.max_interruption_frequency
  min_size                   = var.min_size
  max_size                   = var.max_size
  desired_size               = var.desired_size
  labels                     = local.node_pool_labels
  tenant_id                  = local.tenant_slug
  tags                       = local.tags
}

module "model_serving_primary" {
  source = "../../../../../infra/modules/model-serving"

  environment                      = local.tenant_environment
  namespace                        = "model-serving-${local.tenant_slug}"
  model_artifact_uri               = var.primary_model_artifact_uri
  model_weight_checksum            = var.primary_model_weight_checksum
  tensor_parallel_size             = var.primary_tensor_parallel_size
  gpu_utilization_target           = var.primary_gpu_utilization_target
  replicas                         = var.replicas
  node_pool_name                   = module.gpu_node_pool.node_pool_name
  termination_grace_period_seconds = var.warning_window_seconds - var.termination_grace_buffer_seconds
  tenant_id                        = local.tenant_slug
  tags                             = local.model_serving_labels
}

# Optional second, architecturally-distinct self-hosted reviewer model
# (spec §13.4/§13.7), co-located on this same tenant's dedicated node
# pool -- only created when a caller supplies one (tiers g7e.12xlarge and
# up per §13.3's sizing ladder). Whether *not* supplying one is actually
# acceptable for this tenant (i.e. a frontier-API escalation path is
# configured instead) is validated by the control-plane's
# `tenant_cell.mark_tenant_cell_ready`, not by this IaC layer -- this
# module only wires the deployment in when told to.
module "model_serving_reviewer" {
  count  = var.reviewer_model_artifact_uri != null ? 1 : 0
  source = "../../../../../infra/modules/model-serving"

  environment                      = "${local.tenant_environment}-reviewer"
  namespace                        = "model-serving-${local.tenant_slug}"
  model_artifact_uri               = var.reviewer_model_artifact_uri
  model_weight_checksum            = var.reviewer_model_weight_checksum
  tensor_parallel_size             = var.reviewer_tensor_parallel_size
  gpu_utilization_target           = var.reviewer_gpu_utilization_target
  replicas                         = var.replicas
  node_pool_name                   = module.gpu_node_pool.node_pool_name
  termination_grace_period_seconds = var.warning_window_seconds - var.termination_grace_buffer_seconds
  tenant_id                        = local.tenant_slug
  tags                             = local.model_serving_labels
}
