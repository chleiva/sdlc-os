# secrets/gcp-secret-manager — Phase 1.5 stub.
#
# Real implementation would declare google_secret_manager_secret containers
# (no versions/values — same boundary as secrets/aws-secrets-manager) and
# google_secret_manager_secret_iam_member read grants. This file only
# satisfies the module contract; it provisions nothing.

terraform {
  required_version = ">= 1.6.0"
  # Real implementation would add:
  #   google = { source = "hashicorp/google", version = ">= 5.0" }
}

locals {
  not_implemented = "secrets/gcp-secret-manager is a Phase 1.5 stub — see README.md"
}
