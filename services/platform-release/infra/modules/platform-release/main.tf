# See variables.tf for why `hashicorp/local` stands in for a real cloud
# provider here. This module's only resource is the deployed-version
# manifest itself -- a real OpenTofu-managed artifact whose content is a
# pure function of the module's inputs, so a plan/apply against a
# changed `platform_version` shows a genuine in-place update (not a
# no-op), and a rollback (re-applying a prior `platform_version`) shows
# the same in-place update in reverse -- both are real `tofu apply`
# runs, not something the Python side fabricates.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
  }
}

locals {
  manifest = {
    environment        = var.environment
    platform_version   = var.platform_version
    component_versions = var.component_versions
    release_id         = var.release_id
    rolled_back_from   = var.rolled_back_from
    is_rollback        = var.rolled_back_from != null
  }

  output_dir = coalesce(var.output_dir, path.module)
}

# The one real, versioned artifact this module manages: "what version is
# the platform's control plane currently running." A cloud deployment of
# the same shape would instead be a container-image tag / Helm release
# version on the orchestrator/dispatcher/registry-service Deployments
# (infra/modules/orchestrator, infra/modules/model-serving in F1's real
# tree) -- this resource is that same idea, standing on the `local`
# provider so it is genuinely `tofu apply`-able without cloud credentials.
resource "local_file" "deployed_version" {
  filename = "${local.output_dir}/deployed-version.json"
  content  = jsonencode(local.manifest)
}
