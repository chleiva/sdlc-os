# Provider configuration for THIS VALIDATION COMPOSITION ONLY -- never
# apply this against a real account. A real environments/* composition
# authenticates the same way environments/pilot-aws-g7e/providers.tf
# does: a short-lived `aws eks get-token` exec-plugin token against a
# real EKS cluster's real endpoint/CA, region driven by the live spot-
# discovery pre-flight step (spec §14.8) -- never a static credential.
#
# This composition has no real cloud account or Kubernetes cluster to
# point at (this deliverable's hard constraint -- see this directory's
# README.md), so its provider blocks use OpenTofu's own documented
# escape hatches for "let me validate/plan this configuration's shape
# without a real backend to talk to": fake, obviously-non-functional
# static credentials plus `skip_credentials_validation`/
# `skip_requesting_account_id` for the aws provider (both provider-level
# settings that exist specifically for this kind of offline
# testing/CI use, never a production credential path), and a
# provably-unreachable placeholder host for kubernetes/helm. `tofu plan`
# on this composition therefore genuinely computes and displays the
# aws_iam_*/local-value plan for both tenants (proving the
# no-shared-node naming claim in README.md for real), then errors on the
# kubernetes_manifest resources the moment they'd need to reach that
# placeholder host -- documented, not hidden, in README.md.

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "validation-only-not-a-real-credential"
  secret_key                  = "validation-only-not-a-real-credential"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
}

provider "kubernetes" {
  host = "https://tenant-cell-validation.invalid:6443" # deliberately unreachable -- see header comment
}

provider "helm" {
  kubernetes = {
    host = "https://tenant-cell-validation.invalid:6443"
  }
}
